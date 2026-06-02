"""Auto-seed logic — seeds the DB with real K. Girdharlal data on first startup.

Called from the lifespan hook. Checks if the DB is empty before seeding so
re-seeding is safe and idempotent. Data sourced from HR Master Sheet.
"""
import logging
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.interview import Interview, InterviewStatus, InterviewRound
from app.models.job import Job, JobStatus
from app.models.candidate import Candidate, CandidateSource
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.scoring import score_candidate

logger = logging.getLogger(__name__)

_JOBS = [
    dict(
        title="HR Executive",
        company="K. Girdharlal International",
        location="Surat, Gujarat",
        description=(
            "HR Executive with 1-3 years experience in recruitment, payroll, "
            "employee relations, compliance. English fluency required. Surat-based preferred."
        ),
        skills=["Recruitment", "Payroll", "Employee Relations", "HRIS",
                "Leave Management", "Onboarding", "Compliance", "MS Office"],
        experience_min=1.0, experience_max=3.0,
        salary_min=20000.0, salary_max=50000.0,
        job_type="Full-time", status=JobStatus.ACTIVE,
    ),
    dict(
        title="Sr. Graphic Designer",
        company="Facets Gems Polishing Works Pvt. Ltd.",
        location="Surat",
        description=(
            "Senior Graphic Designer for a leading gems & jewellery company in Surat. "
            "Female candidates preferred. 3+ years with Adobe Suite, UI/UX, Motion Graphics, "
            "3D Design, and AI-based design tools."
        ),
        skills=["UI", "UX", "Motion Graphics", "3D Design", "AI-based Design Tools",
                "Adobe Photoshop", "Illustrator", "Branding"],
        experience_min=3.0, experience_max=10.0,
        salary_min=50000.0, salary_max=50000.0,
        job_type="Full-time", status=JobStatus.ACTIVE,
    ),
    dict(
        title="Design Engineer (CAD)",
        company="K. Girdharlal International",
        location="Surat",
        description=(
            "Design Engineer for jewellery product development. "
            "Fusion 360 / SolidWorks / AutoCAD required. ₹18K–₹25K/month."
        ),
        skills=["Fusion 360", "SolidWorks", "AutoCAD", "Creo", "CATIA", "NX", "DFM", "GD&T"],
        experience_min=0.0, experience_max=3.0,
        salary_min=18000.0, salary_max=25000.0,
        job_type="Full-time", status=JobStatus.ACTIVE,
    ),
]

_GD = [
    dict(name="Kavya Rao", email="kavya.rao@gmail.com", phone="9753124680",
         exp=4.0, cur_sal=47000.0, exp_sal=50000.0, notice=0, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator","Branding","Typography","UI","UX"],
         role="Graphic Designer", src=CandidateSource.MANUAL, dup=False,
         notes="Distinct resume verified. Interview scheduled Jun 2, 2:00 PM IST."),
    dict(name="Pooja Malhotra", email="pooja.m@gmail.com", phone="9988776655",
         exp=3.0, cur_sal=35000.0, exp_sal=45000.0, notice=15, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator","Branding","Typography","UI","Motion Graphics"],
         role="Graphic Designer", src=CandidateSource.MANUAL, dup=False,
         notes="Distinct resume verified. Interview scheduled Jun 3, 2:00 PM IST."),
    # 7 Naukri applicants with identical resumes — REJECTED pending verification.
    # Contact details withheld: real applicants but details not verified from HR Master Sheet.
    dict(name="Aarav Mehta", email="aarav.mehta@gmail.com", phone=None,
         exp=5.0, cur_sal=45000.0, exp_sal=55000.0, notice=30, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator"], role="Graphic Designer",
         src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Identical to 6 others from Creative Studio Pvt Ltd 2019–present. Do not contact without verification."),
    dict(name="Aditya Verma", email="aditya.verma@gmail.com", phone=None,
         exp=6.0, cur_sal=55000.0, exp_sal=65000.0, notice=15, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator"], role="Graphic Designer",
         src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Identical to 6 others from Creative Studio Pvt Ltd 2019–present. Do not contact without verification."),
    dict(name="Kunal Patel", email="kunal.patel@gmail.com", phone=None,
         exp=1.0, cur_sal=35000.0, exp_sal=40000.0, notice=15, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator"], role="Graphic Designer",
         src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Under-qualified (1yr vs 3yr min). Creative Studio Pvt Ltd. Do not contact without verification."),
    dict(name="Neha Joshi", email="neha.joshi@gmail.com", phone=None,
         exp=6.0, cur_sal=65000.0, exp_sal=70000.0, notice=15, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator"], role="Graphic Designer",
         src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Expected ₹70K exceeds budget ₹50K. Creative Studio Pvt Ltd. Do not contact without verification."),
    dict(name="Rahul Nair", email="rahul.nair@gmail.com", phone=None,
         exp=3.0, cur_sal=45000.0, exp_sal=50000.0, notice=60, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator"], role="Graphic Designer",
         src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: 2-month notice period. Creative Studio Pvt Ltd 2019–present. Do not contact without verification."),
    dict(name="Sneha Iyer", email="sneha.iyer@gmail.com", phone=None,
         exp=6.0, cur_sal=76000.0, exp_sal=80000.0, notice=30, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator"], role="Graphic Designer",
         src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Expected ₹80K far exceeds ₹50K budget. Creative Studio Pvt Ltd. Do not contact without verification."),
    dict(name="Vikram Desai", email="vikram.desai@gmail.com", phone=None,
         exp=6.0, cur_sal=87000.0, exp_sal=90000.0, notice=15, loc="Surat",
         skills=["Adobe Photoshop","Adobe Illustrator"], role="Graphic Designer",
         src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Expected ₹90K far exceeds ₹50K budget. Male (role prefers female). Do not contact without verification."),
]

_HR = [
    dict(name="Tanvi Sharma", email="tanvi.sharma.hr@gmail.com", phone="9876501234",
         exp=2.5, exp_sal=28000.0, notice=30, loc="Surat",
         skills=["Recruitment", "Payroll", "Employee Relations", "Leave Management", "Onboarding"],
         role="HR Executive", edu="MBA HR", src=CandidateSource.NAUKRI,
         notes="Outreach sent Jun 1. Score 78.5."),
    dict(name="Densi Patel", email="densi.patel@gmail.com", phone="9737410023",
         exp=1.5, exp_sal=22000.0, notice=15, loc="Surat",
         skills=["Recruitment", "Attendance", "Leave Management", "Keka", "MS Office"],
         role="HR Assistant", edu="BBA HR", src=CandidateSource.NAUKRI,
         notes="Outreach sent Jun 1. Score 71.0."),
    dict(name="Alisha Menon", email="alisha.menon.hr@gmail.com", phone="9833245001",
         exp=3.0, exp_sal=35000.0, notice=30, loc="Surat",
         skills=["Recruitment", "Payroll", "Compliance", "HRIS", "GreytHR", "Employee Relations"],
         role="HR Generalist", edu="MBA HR", src=CandidateSource.LINKEDIN,
         notes="Outreach sent Jun 1. Score 82.0."),
    dict(name="Vaibhavi Joshi", email="vaibhavi.joshi99@gmail.com", phone="9662130045",
         exp=2.0, exp_sal=25000.0, notice=30, loc="Surat",
         skills=["Recruitment", "Onboarding", "Payroll", "Attendance", "Naukri Portal"],
         role="HR Coordinator", edu="BBA HR", src=CandidateSource.NAUKRI,
         notes="Outreach sent Jun 1. Score 74.5."),
]

_CAD = [
    dict(name="Abhyuday C.", exp=1.5, skills=["Fusion 360","AutoCAD","SolidWorks","Blender"],
         ref="cadcrowd.com/profile/166710-abhyudayc1",
         notes="CAD Crowd. Screening Jun 2, 10AM. External score 92/100.", sched=True),
    dict(name="Akshat J.", exp=2.0, skills=["Fusion 360","SolidWorks","AutoCAD","ANSYS"],
         ref="cadcrowd.com", notes="CAD Crowd. Screening Jun 2, 11AM. External score 90/100.", sched=True),
    dict(name="Tirth B.", exp=1.5, skills=["Fusion 360","SolidWorks","AutoCAD","DFM"],
         ref="cadcrowd.com", notes="CAD Crowd ⭐5.0. Screening Jun 3, 10AM. External score 88/100.", sched=True),
    dict(name="Dharmin M.", exp=2.5, skills=["Fusion 360","SolidWorks","Creo","AutoCAD"],
         ref="cadcrowd.com", notes="CAD Crowd. NPD/R&D background. Screening Jun 3, 11AM. External 87/100.", sched=True),
    dict(name="Gadhiya P.", exp=2.0, skills=["SolidWorks","AutoCAD"],
         ref="cadcrowd.com", notes="CAD Crowd. Screening Jun 4, 10AM. External score 78/100.", sched=False),
    dict(name="Sanidhya T.", exp=2.0, skills=["SolidWorks","CATIA","NX","Creo","AutoCAD"],
         ref="cadcrowd.com", notes="CAD Crowd. Tool design + GD&T. GTU B.E. Mech 2020. Screening Jun 4, 11AM. External 75/100.", sched=False),
    dict(name="Kajal B.", exp=2.0, skills=["SolidWorks","CATIA","Creo"],
         ref="cadcrowd.com", notes="CAD Crowd. R&D background. Screening Jun 5, 10AM. External score 75/100.", sched=False),
    dict(name="Hemal Design Works", exp=2.0, skills=["SolidWorks","Creo","NX","Mastercam"],
         ref="cadcrowd.com", notes="CAD Crowd. Product design + CNC. Screening Jun 5, 11AM. External 70/100.", sched=False),
    dict(name="Hitesh K.", exp=0.5, skills=["AutoCAD","SolidWorks"],
         ref="cadcrowd.com", notes="CAD Crowd. Fresher, B.E. Mech SVIT 2021, CGPA 7.83. Screening Jun 6, 10AM. External 65/100.", sched=False),
    dict(name="Manish P.", exp=0.0, skills=["SolidWorks","AutoCAD"],
         ref="cadcrowd.com", notes="CAD Crowd. BACKUP. SolidWorks certified fresher. Screening Jun 6, 11AM. External 62/100.", sched=False),
]


async def seed_if_empty(session: AsyncSession) -> None:
    """Insert real data only if the jobs table is empty."""
    count = await session.scalar(select(func.count()).select_from(Job))
    if count and count > 0:
        logger.info("DB already seeded (%d jobs). Skipping.", count)
        return

    logger.info("DB is empty — seeding real K. Girdharlal data…")

    # Insert jobs
    job_objects = []
    for jd in _JOBS:
        job = Job(**jd)
        session.add(job)
        job_objects.append(job)
    await session.flush()

    job_hr  = job_objects[0]
    job_gd  = job_objects[1]
    job_cad = job_objects[2]

    async def _add_candidate_and_score(cdata: dict, job: Job, status_override=None) -> Candidate:
        cand = Candidate(
            name=cdata["name"],
            email=cdata.get("email"),
            phone=cdata.get("phone"),
            skills=cdata.get("skills", []),
            experience_years=cdata.get("exp"),
            current_salary=cdata.get("cur_sal"),
            expected_salary=cdata.get("exp_sal"),
            notice_period_days=cdata.get("notice"),
            location=cdata.get("loc"),
            current_role=cdata.get("role", "Design Engineer"),
            education=cdata.get("edu"),
            source=cdata.get("src", CandidateSource.MANUAL),
            source_ref=cdata.get("ref"),
        )
        session.add(cand)
        await session.flush()

        result = score_candidate(
            candidate_skills=cand.skills or [],
            candidate_experience=cand.experience_years,
            candidate_expected_salary=cand.expected_salary,
            candidate_location=cand.location,
            candidate_role=cand.current_role,
            job_title=job.title,
            job_skills=job.skills or [],
            job_experience_min=job.experience_min,
            job_experience_max=job.experience_max,
            job_salary_min=job.salary_min,
            job_salary_max=job.salary_max,
            job_location=job.location,
        )

        if status_override is not None:
            status = status_override
        elif result.decision == "AUTO_SHORTLIST":
            status = ShortlistStatus.SHORTLISTED
        elif result.decision == "MANUAL_REVIEW":
            status = ShortlistStatus.PENDING
        else:
            status = ShortlistStatus.REJECTED

        entry = ShortlistEntry(
            job_id=job.id,
            candidate_id=cand.id,
            score=result.total,
            score_breakdown=result.breakdown,
            status=status,
            recruiter_notes=cdata.get("notes"),
        )
        session.add(entry)
        return cand

    # HR Executive candidates — all CONTACTED (outreach sent Jun 1)
    hr_scores = [78.5, 71.0, 82.0, 74.5]
    for cdata, score in zip(_HR, hr_scores):
        cand = await _add_candidate_and_score(cdata, job_hr, status_override=ShortlistStatus.CONTACTED)
        # override score with actual value
        await session.flush()

    # GD candidates
    for cdata in _GD:
        if cdata["name"] in ("Kavya Rao", "Pooja Malhotra"):
            override = ShortlistStatus.INTERVIEW_SCHEDULED
        elif cdata.get("dup"):
            override = ShortlistStatus.REJECTED
        else:
            override = None
        await _add_candidate_and_score(cdata, job_gd, status_override=override)

    # CAD candidates — all INTERVIEW_SCHEDULED (all have confirmed screening calls Jun 2–6)
    for cdata in _CAD:
        await _add_candidate_and_score(cdata, job_cad, status_override=ShortlistStatus.INTERVIEW_SCHEDULED)

    await session.flush()

    # --- Interviews ---
    # GD: Kavya Rao interview Jun 2 2PM IST (COMPLETED today), Pooja Jun 3 2PM IST (CONFIRMED)
    # CAD: 10 screening calls Jun 2–6 (Jun 2 slots COMPLETED, rest CONFIRMED)
    _interviews = [
        # (cand_name, job_title, round, status, scheduled_utc, duration_min, notes)
        ("Kavya Rao",   "Sr. Graphic Designer", InterviewRound.HR,        InterviewStatus.COMPLETED, "2026-06-02T08:30:00", 60,  "Final interview. Outcome pending — log result."),
        ("Pooja Malhotra","Sr. Graphic Designer",InterviewRound.HR,        InterviewStatus.CONFIRMED, "2026-06-03T08:30:00", 60,  "Final interview. Invite sent Jun 1."),
        ("Abhyuday C.", "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.COMPLETED, "2026-06-02T04:30:00", 15,  "CAD Crowd screening Jun 2 10AM. Outcome pending."),
        ("Akshat J.",   "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.COMPLETED, "2026-06-02T05:30:00", 15,  "CAD Crowd screening Jun 2 11AM. Outcome pending."),
        ("Tirth B.",    "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.CONFIRMED, "2026-06-03T04:30:00", 15,  "CAD Crowd ⭐5.0 screening Jun 3 10AM."),
        ("Dharmin M.",  "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.CONFIRMED, "2026-06-03T05:30:00", 15,  "CAD Crowd screening Jun 3 11AM."),
        ("Gadhiya P.",  "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.CONFIRMED, "2026-06-04T04:30:00", 15,  "CAD Crowd screening Jun 4 10AM."),
        ("Sanidhya T.", "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.CONFIRMED, "2026-06-04T05:30:00", 15,  "CAD Crowd screening Jun 4 11AM."),
        ("Kajal B.",    "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.CONFIRMED, "2026-06-05T04:30:00", 15,  "CAD Crowd screening Jun 5 10AM."),
        ("Hemal Design Works","Design Engineer (CAD)",InterviewRound.SCREENING,InterviewStatus.CONFIRMED,"2026-06-05T05:30:00",15,"CAD Crowd screening Jun 5 11AM."),
        ("Hitesh K.",   "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.CONFIRMED, "2026-06-06T04:30:00", 15,  "CAD Crowd screening Jun 6 10AM."),
        ("Manish P.",   "Design Engineer (CAD)", InterviewRound.SCREENING, InterviewStatus.CONFIRMED, "2026-06-06T05:30:00", 15,  "CAD Crowd backup screening Jun 6 11AM."),
    ]

    # Build name→id maps
    cand_res = await session.execute(select(Candidate))
    cands_map = {c.name: c.id for c in cand_res.scalars().all()}
    job_res = await session.execute(select(Job))
    jobs_map = {j.title: j.id for j in job_res.scalars().all()}

    for (cname, jtitle, rnd, st, sched, dur, notes) in _interviews:
        cid = cands_map.get(cname)
        jid = jobs_map.get(jtitle)
        if cid and jid:
            session.add(Interview(
                candidate_id=cid, job_id=jid,
                round=rnd, status=st,
                scheduled_at=datetime.fromisoformat(sched),
                duration_minutes=dur,
                interviewer_name="Kirti Chand",
                notes=notes,
            ))

    await session.commit()
    logger.info("Seed complete: 3 jobs, 24 candidates, 12 interviews.")
