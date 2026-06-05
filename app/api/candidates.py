"""Candidates API — CRUD for candidate records."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.candidate import Candidate
from app.schemas.candidate import CandidateCreate, CandidateRead, CandidateUpdate

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.post("", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
async def create_candidate(payload: CandidateCreate, db: AsyncSession = Depends(get_db)):
    candidate = Candidate(**payload.model_dump())
    db.add(candidate)
    await db.flush()
    return candidate


@router.get("", response_model=list[CandidateRead])
async def list_candidates(
    source: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    query = select(Candidate).offset(skip).limit(limit).order_by(Candidate.created_at.desc())
    if source:
        query = query.where(Candidate.source == source)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/quality-issues")
async def quality_issues(limit: int = 1000, db: AsyncSession = Depends(get_db)):
    """Candidate records that need a human to fix them — bad/missing contact info,
    bounced emails, junk salary values, or too little data to score."""
    from app.services.data_quality import analyze_candidates
    from app.models.outreach import OutreachLog, OutreachStatus

    res = await db.execute(
        select(Candidate).order_by(Candidate.created_at.desc()).limit(limit)
    )
    candidates = res.scalars().all()

    bres = await db.execute(
        select(OutreachLog.candidate_id).where(OutreachLog.status == OutreachStatus.BOUNCED)
    )
    bounced = frozenset(row[0] for row in bres.all())
    return analyze_candidates(candidates, bounced)


@router.get("/duplicates")
async def detect_duplicates(limit: int = 1000, db: AsyncSession = Depends(get_db)):
    """Find likely duplicates — the same person applying twice (shared email/phone)
    or different people submitting copy-pasted resumes (identical resume text)."""
    from app.services.duplicates import find_duplicates

    res = await db.execute(
        select(Candidate).order_by(Candidate.created_at.desc()).limit(limit)
    )
    return find_duplicates(res.scalars().all())


@router.get("/{candidate_id}", response_model=CandidateRead)
async def get_candidate(candidate_id: int, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(candidate_id, db)


@router.get("/{candidate_id}/duplicates")
async def candidate_duplicates(candidate_id: int, db: AsyncSession = Depends(get_db)):
    """Duplicate clusters that include this candidate — powers the profile warning."""
    from app.services.duplicates import find_duplicates

    res = await db.execute(
        select(Candidate).order_by(Candidate.created_at.desc()).limit(1000)
    )
    result = find_duplicates(res.scalars().all())
    mine = [
        cl for cl in result["same_contact"] + result["same_resume"]
        if any(m["id"] == candidate_id for m in cl["candidates"])
    ]
    return {"clusters": mine}


@router.post("/{candidate_id}/merge")
async def merge_candidate(
    candidate_id: int, duplicate_id: int, db: AsyncSession = Depends(get_db)
):
    """Merge a duplicate record into this one: move its history here, fill any
    blank fields from it, then delete the duplicate. Keeps the lower-id record."""
    from app.models.shortlist import ShortlistEntry
    from app.models.outreach import OutreachLog
    from app.models.interview import Interview

    if candidate_id == duplicate_id:
        raise HTTPException(status_code=400, detail="Cannot merge a candidate into itself.")

    keep = await _get_or_404(candidate_id, db)
    dupe = await _get_or_404(duplicate_id, db)

    # Job IDs the kept candidate already has a shortlist entry for
    keep_sl = await db.execute(
        select(ShortlistEntry.job_id).where(ShortlistEntry.candidate_id == keep.id)
    )
    keep_job_ids = {row[0] for row in keep_sl.all()}

    # Move the duplicate's shortlist entries over (skip jobs already covered)
    dupe_sl = await db.execute(
        select(ShortlistEntry).where(ShortlistEntry.candidate_id == dupe.id)
    )
    for entry in dupe_sl.scalars().all():
        if entry.job_id in keep_job_ids:
            await db.delete(entry)
        else:
            entry.candidate_id = keep.id

    # Move outreach + interview history
    for Model in (OutreachLog, Interview):
        rows = await db.execute(select(Model).where(Model.candidate_id == dupe.id))
        for r in rows.scalars().all():
            r.candidate_id = keep.id

    # Fill any blank fields on the kept record from the duplicate
    for field in (
        "email", "phone", "whatsapp", "location", "current_role", "current_employer",
        "education", "experience_years", "expected_salary", "current_salary",
        "notice_period_days", "raw_profile", "resume_url",
    ):
        if not getattr(keep, field) and getattr(dupe, field):
            setattr(keep, field, getattr(dupe, field))

    merged_skills = list({*(keep.skills or []), *(dupe.skills or [])})
    if merged_skills:
        keep.skills = merged_skills

    await db.delete(dupe)
    await db.flush()
    return {"ok": True, "kept_id": keep.id, "removed_id": duplicate_id}


@router.patch("/{candidate_id}", response_model=CandidateRead)
async def update_candidate(
    candidate_id: int, payload: CandidateUpdate, db: AsyncSession = Depends(get_db)
):
    candidate = await _get_or_404(candidate_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(candidate, field, value)
    return candidate


@router.delete("/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(candidate_id: int, db: AsyncSession = Depends(get_db)):
    candidate = await _get_or_404(candidate_id, db)
    await db.delete(candidate)


@router.get("/{candidate_id}/profile")
async def get_candidate_profile(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Full candidate profile: contact info + all jobs + outreach history + interviews + AI insights."""
    from app.models.shortlist import ShortlistEntry
    from app.models.job import Job
    from app.models.outreach import OutreachLog
    from app.models.interview import Interview

    candidate = await db.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Shortlist entries with job details
    sl_res = await db.execute(
        select(ShortlistEntry).where(ShortlistEntry.candidate_id == candidate_id)
        .order_by(ShortlistEntry.created_at.desc())
    )
    entries = sl_res.scalars().all()

    job_ids = list({e.job_id for e in entries})
    jobs_map = {}
    if job_ids:
        jr = await db.execute(select(Job).where(Job.id.in_(job_ids)))
        jobs_map = {j.id: j for j in jr.scalars().all()}

    shortlist_data = []
    for e in entries:
        j = jobs_map.get(e.job_id)
        bd = e.score_breakdown or {}
        shortlist_data.append({
            "id": e.id,
            "job_id": e.job_id,
            "job_title": j.title if j else f"Job #{e.job_id}",
            "job_company": j.company if j else "",
            "status": e.status.value,
            "score": e.score,
            "recruiter_notes": e.recruiter_notes,
            "ai_strengths": bd.get("ai_strengths", []),
            "ai_concerns": bd.get("ai_concerns", []),
            "ai_reasoning": bd.get("ai_reasoning", ""),
            "ai_opener": bd.get("ai_opener", ""),
            "score_breakdown": {k: v for k, v in bd.items() if not k.startswith("ai_")},
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        })

    # Outreach logs
    ol_res = await db.execute(
        select(OutreachLog).where(OutreachLog.candidate_id == candidate_id)
        .order_by(OutreachLog.created_at.desc())
        .limit(50)
    )
    outreach_data = []
    for o in ol_res.scalars().all():
        outreach_data.append({
            "id": o.id,
            "job_id": o.job_id,
            "channel": o.channel.value,
            "type": o.outreach_type.value,
            "status": o.status.value,
            "message": (o.message or "")[:300],
            "reply": o.reply_text,
            "sent_at": o.sent_at.isoformat() if o.sent_at else None,
            "replied_at": o.replied_at.isoformat() if o.replied_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    # Interviews
    iv_res = await db.execute(
        select(Interview).where(Interview.candidate_id == candidate_id)
        .order_by(Interview.created_at.desc())
    )
    interview_data = []
    for i in iv_res.scalars().all():
        interview_data.append({
            "id": i.id,
            "job_id": i.job_id,
            "round": i.round.value,
            "status": i.status.value,
            "scheduled_at": i.scheduled_at.isoformat() if i.scheduled_at else None,
            "meet_link": i.meet_link,
            "notes": i.notes,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        })

    # Build chronological timeline
    timeline = []
    for o in outreach_data:
        ts = o.get("sent_at") or o.get("created_at")
        timeline.append({"ts": ts, "type": "outreach", "channel": o["channel"], "status": o["status"], "detail": o["type"]})
        if o.get("reply"):
            timeline.append({"ts": o.get("replied_at") or ts, "type": "reply", "detail": (o["reply"] or "")[:100]})
    for i in interview_data:
        ts = i.get("scheduled_at") or i.get("created_at")
        timeline.append({"ts": ts, "type": "interview", "round": i["round"], "status": i["status"]})
    timeline.sort(key=lambda x: x.get("ts") or "", reverse=True)

    return {
        "id": candidate.id,
        "name": candidate.name,
        "email": candidate.email,
        "phone": candidate.phone,
        "whatsapp": candidate.whatsapp,
        "location": candidate.location,
        "current_role": candidate.current_role,
        "current_employer": candidate.current_employer,
        "experience_years": candidate.experience_years,
        "expected_salary": candidate.expected_salary,
        "current_salary": candidate.current_salary,
        "notice_period_days": candidate.notice_period_days,
        "skills": candidate.skills or [],
        "education": candidate.education,
        "source": candidate.source.value if candidate.source else None,
        "source_ref": candidate.source_ref,
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        "shortlist": shortlist_data,
        "outreach": outreach_data,
        "interviews": interview_data,
        "timeline": timeline[:30],
    }


async def _get_or_404(candidate_id: int, db: AsyncSession) -> Candidate:
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id} not found")
    return obj
