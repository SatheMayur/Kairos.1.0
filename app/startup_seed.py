"""Auto-seed logic — seeds the DB with real K. Girdharlal data on first startup.

Called from the lifespan hook. Checks if the DB is empty before seeding so
re-seeding is safe and idempotent. Data sourced from HR Master Sheet.
"""
import logging
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.job import Job, JobStatus
from app.models.candidate import Candidate, CandidateSource
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.scoring import score_candidate

logger = logging.getLogger(__name__)

_JOBS = [
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
    dict(name="Aarav Mehta", email="aarav.mehta@gmail.com", phone="9876543210",
         exp=5.0, cur_sal=45000.0, exp_sal=55000.0, notice=30, loc="Surat",
         skills=[], role="Graphic Designer", src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Identical to 7 others. Employer: Creative Studio Pvt Ltd 2019–present."),
    dict(name="Aditya Verma", email="aditya.verma@gmail.com", phone="9871209876",
         exp=6.0, cur_sal=55000.0, exp_sal=65000.0, notice=15, loc="Surat",
         skills=[], role="Graphic Designer", src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Identical to 7 others. Employer: Creative Studio Pvt Ltd 2019–present."),
    dict(name="Kunal Patel", email="kunal.patel@gmail.com", phone="9898123456",
         exp=1.0, cur_sal=35000.0, exp_sal=40000.0, notice=15, loc="Surat",
         skills=[], role="Graphic Designer", src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Under-qualified (1yr vs 3yr min). Creative Studio Pvt Ltd."),
    dict(name="Neha Joshi", email="neha.joshi@gmail.com", phone="9812345678",
         exp=6.0, cur_sal=65000.0, exp_sal=70000.0, notice=15, loc="Surat",
         skills=[], role="Graphic Designer", src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Expected ₹70K exceeds budget ₹50K. Creative Studio Pvt Ltd."),
    dict(name="Rahul Nair", email="rahul.nair@gmail.com", phone="9887654321",
         exp=3.0, cur_sal=45000.0, exp_sal=50000.0, notice=60, loc="Surat",
         skills=[], role="Graphic Designer", src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: 2-month notice. Creative Studio Pvt Ltd 2019–present."),
    dict(name="Riya Shah", email="riya.shah@gmail.com", phone="9825012345",
         exp=2.0, cur_sal=35000.0, exp_sal=45000.0, notice=60, loc="Surat",
         skills=[], role="Graphic Designer", src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Flagged in Master Sheet. Under-qualified (2yr vs 3yr min)."),
    dict(name="Sneha Iyer", email="sneha.iyer@gmail.com", phone="9765432109",
         exp=6.0, cur_sal=76000.0, exp_sal=80000.0, notice=30, loc="Surat",
         skills=[], role="Graphic Designer", src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Expected ₹80K far exceeds ₹50K budget. Creative Studio Pvt Ltd."),
    dict(name="Vikram Desai", email="vikram.desai@gmail.com", phone="9909012345",
         exp=6.0, cur_sal=87000.0, exp_sal=90000.0, notice=15, loc="Surat",
         skills=[], role="Graphic Designer", src=CandidateSource.NAUKRI, dup=True,
         notes="DUPLICATE RESUME RISK: Expected ₹90K far exceeds ₹50K budget. Male (role prefers female)."),
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

    job_gd = job_objects[0]
    job_cad = job_objects[1]

    async def _add_candidate_and_score(cdata: dict, job: Job, status_override=None):
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

        session.add(ShortlistEntry(
            job_id=job.id,
            candidate_id=cand.id,
            score=result.total,
            score_breakdown=result.breakdown,
            status=status,
            recruiter_notes=cdata.get("notes"),
        ))

    for cdata in _GD:
        if cdata["name"] in ("Kavya Rao", "Pooja Malhotra"):
            override = ShortlistStatus.INTERVIEW_SCHEDULED
        elif cdata.get("dup"):
            override = ShortlistStatus.REJECTED
        else:
            override = None
        await _add_candidate_and_score(cdata, job_gd, status_override=override)

    for cdata in _CAD:
        override = ShortlistStatus.INTERVIEW_SCHEDULED if cdata.get("sched") else None
        await _add_candidate_and_score(cdata, job_cad, status_override=override)

    await session.commit()
    logger.info("Seed complete: 2 jobs, 20 candidates.")
