"""Candidates API — CRUD for candidate records."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.candidate import Candidate
from app.schemas.candidate import CandidateCreate, CandidateRead, CandidateUpdate

router = APIRouter(prefix="/candidates", tags=["candidates"])


# ── Inbound applicant capture ────────────────────────────────────────────────
# Candidates who APPLY to your job postings arrive with real contact details
# (Naukri / WorkIndia / Indeed application emails land in Gmail). An agent with
# Gmail access parses those and posts them here. This is NOT the disabled bulk
# import — it's warm inbound leads, gated on reachability and de-duplicated.

class ApplicantIn(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    source: str = "WORKINDIA"
    current_role: Optional[str] = None
    current_employer: Optional[str] = None
    experience_years: Optional[float] = None
    expected_salary: Optional[float] = None
    location: Optional[str] = None
    skills: Optional[list[str]] = None
    raw_profile: Optional[str] = None


class IngestApplicantsIn(BaseModel):
    applicants: list[ApplicantIn]
    job_id: Optional[int] = None        # if set, score + shortlist against this job
    require_both: bool = False          # True → need email AND phone (else either is fine)
    outreach: bool = False              # True → WhatsApp-first outbound to each captured reachable applicant


@router.post("/ingest-applicants")
async def ingest_applicants(payload: IngestApplicantsIn, db: AsyncSession = Depends(get_db)):
    """Add inbound applicants (from your Gmail) as candidates. Skips anyone with no
    usable contact, de-dupes on email/phone, and (if job_id given) scores + shortlists
    them so they enter that job's pipeline."""
    from app.services.data_quality import is_reachable_contact, has_email_and_phone
    from app.models.candidate import CandidateSource
    from app.models.job import Job
    from app.utils.phone import normalize_indian_mobile

    gate = has_email_and_phone if payload.require_both else is_reachable_contact
    job = await db.get(Job, payload.job_id) if payload.job_id else None
    if payload.job_id and not job:
        raise HTTPException(status_code=404, detail=f"Job {payload.job_id} not found")

    added = duplicates = skipped_no_contact = scored = 0
    results = []
    touched_ids: list[int] = []         # candidates we added/updated this run (for outbound)
    for a in payload.applicants:
        if not gate(a.email, a.phone, a.whatsapp):
            skipped_no_contact += 1
            results.append({"name": a.name, "result": "SKIPPED_NO_CONTACT"})
            continue

        # De-dupe: match an existing candidate by email or by normalized mobile.
        cand = None
        if a.email:
            cand = (await db.execute(select(Candidate).where(Candidate.email == a.email))).scalar_one_or_none()
        nm = normalize_indian_mobile(a.phone) or normalize_indian_mobile(a.whatsapp)
        if not cand and nm:
            for c in (await db.execute(select(Candidate))).scalars().all():
                if normalize_indian_mobile(c.phone) == nm or normalize_indian_mobile(c.whatsapp) == nm:
                    cand = c
                    break

        if cand:
            duplicates += 1
        else:
            try:
                src = CandidateSource[a.source] if a.source in CandidateSource.__members__ else CandidateSource.MANUAL
            except Exception:
                src = CandidateSource.MANUAL
            cand = Candidate(
                name=a.name, email=a.email, phone=a.phone, whatsapp=a.whatsapp or a.phone,
                current_role=a.current_role, current_employer=a.current_employer,
                experience_years=a.experience_years, expected_salary=a.expected_salary,
                location=a.location, skills=a.skills or [], raw_profile=a.raw_profile, source=src,
            )
            db.add(cand)
            await db.flush()
            added += 1

        if job:
            from app.services.sourcing import _score_and_shortlist
            await _score_and_shortlist(cand, job, db)
            scored += 1
        touched_ids.append(cand.id)
        results.append({"name": a.name, "candidate_id": cand.id,
                        "result": "DUPLICATE" if cand and duplicates and not added else "ADDED"})

    await db.commit()

    # Outbound: WhatsApp-first contact to each captured, reachable applicant. They
    # applied, so we reach out even at PENDING and regardless of job pause state.
    contacted = 0
    if payload.outreach and touched_ids:
        from app.services.auto_outreach import contact_candidate_now
        for cid in touched_ids:
            try:
                res = await contact_candidate_now(db, cid)
                contacted += res.get("contacted", 0)
            except Exception:
                pass
        await db.commit()

    return {"added": added, "duplicates": duplicates,
            "skipped_no_contact": skipped_no_contact, "scored": scored,
            "contacted": contacted, "job": job.title if job else None, "details": results}


@router.post("", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
async def create_candidate(payload: CandidateCreate, db: AsyncSession = Depends(get_db)):
    # Data-quality gate: a candidate with no email and no usable mobile can never
    # be contacted — don't add them to the system. Add a working email or 10-digit
    # mobile first.
    from app.services.data_quality import is_reachable_contact
    if not is_reachable_contact(payload.email, payload.phone, payload.whatsapp):
        raise HTTPException(
            status_code=422,
            detail=("Can't add this candidate — there's no working email or phone/WhatsApp number, "
                    "so they could never be contacted. Add a valid email or 10-digit mobile and try again."),
        )
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
    from app.models.shortlist import ShortlistEntry, ShortlistStatus

    res = await db.execute(
        select(Candidate).order_by(Candidate.created_at.desc()).limit(limit)
    )
    candidates = res.scalars().all()

    bres = await db.execute(
        select(OutreachLog.candidate_id).where(OutreachLog.status == OutreachStatus.BOUNCED)
    )
    bounced = frozenset(row[0] for row in bres.all())

    # Candidates who are lined up to be contacted (shortlisted) or waiting for
    # review (pending) — if these have no phone/email they'd silently sit there
    # un-contacted. Surfaces Apna's "contact hidden until unlocked" candidates.
    ares = await db.execute(
        select(ShortlistEntry.candidate_id).where(
            ShortlistEntry.status.in_(
                [ShortlistStatus.SHORTLISTED, ShortlistStatus.PENDING]
            )
        )
    )
    awaiting = frozenset(row[0] for row in ares.all())
    return analyze_candidates(candidates, bounced, awaiting)


@router.post("/reengage-role")
async def reengage_role(
    job_id: int,
    keywords: str = ("ai engineer,artificial intelligence,machine learning,ml engineer,"
                     "data scientist,generative ai,gen ai,llm,deep learning,nlp,ai intern,ai/ml"),
    outreach: bool = False,
    dry_run: bool = True,
    limit: int = 60,
    db: AsyncSession = Depends(get_db),
):
    """Find candidates whose chat/profile was about an AI/ML role (often told 'no
    opening' before the role existed), move them into `job_id`'s pipeline as
    SHORTLISTED, and — if outreach=true — send a recovery message (WhatsApp-first).
    Dry-run by default."""
    from app.models.conversation import Conversation
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.job import Job
    from app.services.data_quality import is_reachable

    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    kws = [k.strip().lower() for k in keywords.split(",") if k.strip()]

    convs = (await db.execute(select(Conversation))).scalars().all()
    matched_ids: set[int] = set()
    for cv in convs:
        text = " ".join((h.get("text") or "") for h in (cv.history or [])).lower()
        if any(k in text for k in kws):
            matched_ids.add(cv.candidate_id)
    cands = {c.id: c for c in (await db.execute(select(Candidate))).scalars().all()}
    for c in cands.values():
        blob = " ".join(filter(None, [c.current_role, c.raw_profile, " ".join(c.skills or [])])).lower()
        if any(k in blob for k in kws):
            matched_ids.add(c.id)
    matched = [cands[i] for i in matched_ids if i in cands][:limit]

    _REOPEN = {ShortlistStatus.PENDING, ShortlistStatus.REJECTED,
               ShortlistStatus.NOT_INTERESTED, ShortlistStatus.DROPPED}
    repointed = 0
    rows = []
    for c in matched:
        e = (await db.execute(select(ShortlistEntry).where(
            ShortlistEntry.candidate_id == c.id, ShortlistEntry.job_id == job_id))).scalar_one_or_none()
        will = "new_shortlist" if not e else ("reopen" if e.status in _REOPEN else f"keep:{e.status.value}")
        rows.append({"id": c.id, "name": c.name, "reachable": is_reachable(c), "action": will})
        if dry_run:
            continue
        if not e:
            db.add(ShortlistEntry(candidate_id=c.id, job_id=job_id, score=0,
                                  status=ShortlistStatus.SHORTLISTED))
            repointed += 1
        elif e.status in _REOPEN:
            e.status = ShortlistStatus.SHORTLISTED
            repointed += 1
    if not dry_run:
        await db.commit()

    contacted = 0
    used_channel = None
    if outreach and not dry_run and matched:
        from app.services.auto_outreach import decide_primary_channel
        from app.services.outreach import send_outreach
        from app.models.outreach import OutreachChannel, OutreachType
        channel, _ = await decide_primary_channel(db)
        used_channel = channel.value
        for c in matched:
            if not is_reachable(c):
                continue
            first = (c.name or "there").split()[0]
            body = (f"Hi {first}! Apologies for the earlier mix-up — we *do* have an "
                    f"*{job.title}* opening at {job.company or 'K. Girdharlal International'}. "
                    f"Given your background, we'd love to take this forward. Are you still "
                    f"interested? If so, please share your current CTC, expected CTC, notice "
                    f"period and current location.")
            try:
                log = await send_outreach(candidate=c, job=job, channel=channel,
                                          outreach_type=OutreachType.INITIAL_CONTACT, body=body, db=db)
                if (log.status.value == "SENT"
                        and log.channel in (OutreachChannel.WHATSAPP, OutreachChannel.EMAIL, OutreachChannel.SMS)):
                    contacted += 1
                    e = (await db.execute(select(ShortlistEntry).where(
                        ShortlistEntry.candidate_id == c.id, ShortlistEntry.job_id == job_id))).scalar_one_or_none()
                    if e and e.status in (ShortlistStatus.SHORTLISTED, ShortlistStatus.PENDING):
                        e.status = ShortlistStatus.CONTACTED
            except Exception:
                pass
        await db.commit()

    return {"dry_run": dry_run, "job": job.title, "matched": len(matched),
            "repointed": repointed, "contacted": contacted,
            "channel": used_channel, "candidates": rows}


@router.post("/backfill-names")
async def backfill_names(confirm: bool = False, llm_limit: int = 12, db: AsyncSession = Depends(get_db)):
    """Fix candidates whose 'name' is really a WhatsApp handle/emoji. For each:
    1) pull a stated name from their chat history (regex), 2) else ask the LLM to
    read the history for a name (capped, the 'powerful' step), 3) else just clean
    the handle (drop emojis/_/@). Dry-run by default; confirm=true applies."""
    from app.utils.names import is_placeholder_name, clean_name, extract_name_from_text
    from app.models.conversation import Conversation
    from app.services.llm import llm_json, llm_provider

    cands = (await db.execute(select(Candidate))).scalars().all()
    targets = [c for c in cands if is_placeholder_name(c.name, c.phone or c.whatsapp)]

    conv_by_cand: dict[int, list] = {}
    for cv in (await db.execute(select(Conversation))).scalars().all():
        conv_by_cand.setdefault(cv.candidate_id, []).append(cv)

    changes, llm_used = [], 0
    for c in targets:
        texts = [h["text"] for cv in conv_by_cand.get(c.id, [])
                 for h in (cv.history or []) if h.get("dir") == "in" and h.get("text")]
        newname, how = None, None
        for t in texts:                                   # 1) stated name
            nm = extract_name_from_text(t)
            if nm:
                newname, how = nm, "stated"
                break
        if not newname and texts and llm_provider() != "none" and llm_used < llm_limit:
            joined = " | ".join(texts)[:800]              # 2) LLM reads the history
            r = await llm_json(
                "From these WhatsApp messages a job candidate sent, what is the "
                "person's full name? If no real name is present, use null. "
                f'Reply ONLY JSON: {{"name": "<name or null>"}}. Messages: {joined}',
                max_tokens=60)
            llm_used += 1
            cand_nm = (r or {}).get("name")
            if cand_nm and str(cand_nm).lower() != "null" and not is_placeholder_name(str(cand_nm)):
                newname = " ".join(w.capitalize() for w in clean_name(str(cand_nm)).split())[:120]
                how = "ai"
        if not newname:                                   # 3) clean the handle
            cl = clean_name(c.name)
            if cl and cl != c.name:
                newname, how = cl[:120], "cleaned"
        if newname and newname != c.name:
            changes.append({"id": c.id, "from": c.name, "to": newname, "how": how})
            if confirm:
                c.name = newname
    if confirm:
        await db.commit()
    return {"dry_run": not confirm, "placeholder_total": len(targets),
            "to_change": len(changes), "llm_used": llm_used, "sample": changes[:60]}


@router.post("/cleanup-no-contact")
async def cleanup_no_contact(confirm: bool = False, db: AsyncSession = Depends(get_db)):
    """Find candidates already in the system with no usable email/phone/WhatsApp.

    Dry-run by default (returns the count + names). Pass confirm=true to remove
    them and their dependent rows. Protective: anyone HIRED or with a real
    scheduled interview is kept (never delete genuine progress)."""
    from sqlalchemy import delete as sa_delete
    from app.services.data_quality import is_reachable
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.outreach import OutreachLog
    from app.models.interview import Interview
    from app.models.conversation import Conversation

    res = await db.execute(select(Candidate))
    candidates = res.scalars().all()
    unreachable = [c for c in candidates if not is_reachable(c)]

    # Protect anyone HIRED or with a real (dated) interview.
    protected_ids: set[int] = set()
    if unreachable:
        ids = [c.id for c in unreachable]
        hired = await db.execute(
            select(ShortlistEntry.candidate_id).where(
                ShortlistEntry.candidate_id.in_(ids),
                ShortlistEntry.status == ShortlistStatus.HIRED,
            )
        )
        protected_ids |= {r[0] for r in hired.all()}
        ivs = await db.execute(
            select(Interview.candidate_id).where(
                Interview.candidate_id.in_(ids),
                Interview.scheduled_at.isnot(None),
            )
        )
        protected_ids |= {r[0] for r in ivs.all()}

    removable = [c for c in unreachable if c.id not in protected_ids]

    if not confirm:
        return {
            "dry_run": True,
            "no_contact_total": len(unreachable),
            "protected_kept": len(protected_ids),
            "would_remove": len(removable),
            "candidates": [{"id": c.id, "name": c.name, "source": c.source.value if c.source else None}
                           for c in removable[:200]],
        }

    removed = 0
    for c in removable:
        for Model in (ShortlistEntry, OutreachLog, Interview, Conversation):
            await db.execute(sa_delete(Model).where(Model.candidate_id == c.id))
        await db.delete(c)
        removed += 1
    await db.commit()
    return {"dry_run": False, "removed": removed, "protected_kept": len(protected_ids)}


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


@router.get("/{candidate_id}/conversation")
async def candidate_conversation(candidate_id: int, db: AsyncSession = Depends(get_db)):
    """Latest WhatsApp conversation thread + facts the agent has collected."""
    from app.models.conversation import Conversation
    res = await db.execute(
        select(Conversation).where(Conversation.candidate_id == candidate_id)
        .order_by(Conversation.updated_at.desc()).limit(1)
    )
    conv = res.scalars().first()
    if not conv:
        return {"exists": False}
    return {
        "exists": True,
        "job_id": conv.job_id,
        "status": conv.status,
        "last_intent": conv.last_intent,
        "needs_human": conv.needs_human,
        "collected": conv.collected or {},
        "history": conv.history or [],
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }


@router.post("/{candidate_id}/contact")
async def contact_candidate(candidate_id: int, db: AsyncSession = Depends(get_db)):
    """Reach out to this candidate now — through the system, on the live channel
    (WhatsApp if connected, else email). Logs the message and advances them to
    CONTACTED. Refuses politely if there's no usable phone/email."""
    from app.services.data_quality import is_reachable
    from app.services.auto_outreach import contact_candidate_now

    candidate = await _get_or_404(candidate_id, db)
    if not is_reachable(candidate):
        raise HTTPException(
            status_code=422,
            detail="This candidate has no usable phone or email yet — add contact info first.",
        )
    result = await contact_candidate_now(db, candidate_id)
    if result.get("reason") == "no_open_pipeline_entry":
        raise HTTPException(
            status_code=422,
            detail="This candidate isn't lined up for any job yet — shortlist them for a job first.",
        )
    return result


@router.post("/{candidate_id}/merge")
async def merge_candidate(
    candidate_id: int, duplicate_id: int, db: AsyncSession = Depends(get_db)
):
    """Merge a duplicate record into this one: move its history here, fill any
    blank fields from it, then delete the duplicate. Keeps the lower-id record."""
    from app.models.shortlist import ShortlistEntry
    from app.models.outreach import OutreachLog
    from app.models.interview import Interview
    from app.models.conversation import Conversation

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

    # Move outreach + interview + conversation history (every table that FKs into
    # candidates.id) so deleting the duplicate cannot orphan a row or violate the FK.
    for Model in (OutreachLog, Interview, Conversation):
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
    """Delete a candidate and all rows that reference it, so the delete can't
    violate the foreign keys (Postgres 500) or orphan dependent rows."""
    from sqlalchemy import delete as sa_delete
    from app.models.shortlist import ShortlistEntry
    from app.models.outreach import OutreachLog
    from app.models.interview import Interview
    from app.models.conversation import Conversation

    candidate = await _get_or_404(candidate_id, db)
    for Model in (ShortlistEntry, OutreachLog, Interview, Conversation):
        await db.execute(sa_delete(Model).where(Model.candidate_id == candidate_id))
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
            "error_detail": o.error_detail,
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
        # Additive: carry the failure reason (when a send failed) and a short
        # message preview so the timeline can show plain-English detail.
        preview = (o.get("message") or "").strip()
        timeline.append({
            "ts": ts, "type": "outreach", "channel": o["channel"],
            "status": o["status"], "detail": o["type"],
            "failure_reason": o.get("error_detail"),
            "preview": (preview[:80] + ("…" if len(preview) > 80 else "")) if preview else None,
        })
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
