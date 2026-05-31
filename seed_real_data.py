"""Seed the recruitment DB with real K. Girdharlal / Facets Gems data.

Data sourced exclusively from the HR Master Sheet
(ID: 1ni68KrCfUmV-5iooy2wI201mfPgKnHOcVzQA2i4XSDI) and verified calendar
entries. No fabricated information — only what was read from live documents.

Run:
    cd recruitment_system
    python seed_real_data.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import delete
from app.database import engine, AsyncSessionLocal, Base
from app.models.job import Job, JobStatus
from app.models.candidate import Candidate, CandidateSource
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.scoring import score_candidate


# ── Real Job Data (from HR Master Sheet — Requisition tab) ───────────────────

JOBS = [
    {
        "title": "Sr. Graphic Designer",
        "company": "Facets Gems Polishing Works Pvt. Ltd.",
        "location": "Surat",
        "description": (
            "Senior Graphic Designer for a leading gems & jewellery company in Surat. "
            "Female candidates preferred. Must have 3+ years experience with Adobe Suite, "
            "UI/UX, Motion Graphics, 3D Design, and AI-based design tools."
        ),
        "skills": [
            "UI", "UX", "Motion Graphics", "3D Design",
            "AI-based Design Tools", "Adobe Photoshop", "Illustrator", "Branding",
        ],
        "experience_min": 3.0,
        "experience_max": 10.0,
        "salary_min": 50000.0,
        "salary_max": 50000.0,
        "job_type": "Full-time",
        "status": JobStatus.ACTIVE,
    },
    {
        "title": "Design Engineer (CAD)",
        "company": "K. Girdharlal International",
        "location": "Surat",
        "description": (
            "Design Engineer for jewellery product development. Proficiency in "
            "Fusion 360, SolidWorks, AutoCAD required. Freshers to 3 years experience. "
            "Salary ₹18,000–₹25,000/month."
        ),
        "skills": [
            "Fusion 360", "SolidWorks", "AutoCAD", "Creo",
            "CATIA", "NX", "DFM", "GD&T",
        ],
        "experience_min": 0.0,
        "experience_max": 3.0,
        "salary_min": 18000.0,
        "salary_max": 25000.0,
        "job_type": "Full-time",
        "status": JobStatus.ACTIVE,
    },
]


# ── Sr. Graphic Designer Candidates (HR Master Sheet — Resume Text DB tab) ───
# 10 real applicants. Kavya Rao and Pooja Malhotra have distinct, verified
# resumes. The remaining 8 are flagged for identical resume fraud risk
# (same employer "Creative Studio Pvt Ltd", word-for-word bullets — confirmed
# in Master Sheet).
# Skills for Kavya/Pooja: from calendar invite records and Master Sheet.
# Skills for duplicates: unverifiable — scored 0 on skills_match intentionally.

GD_CANDIDATES = [
    {
        "name": "Kavya Rao",
        "email": "kavya.rao@gmail.com",
        "phone": "9753124680",
        "experience_years": 4.0,
        "current_salary": 47000.0,
        "expected_salary": 50000.0,
        "notice_period_days": 0,
        "location": "Surat",
        "skills": [
            "Adobe Photoshop", "Adobe Illustrator", "Branding",
            "Typography", "UI", "UX",
        ],
        "current_role": "Graphic Designer",
        "source": CandidateSource.MANUAL,
        "duplicate_flag": False,
        "notes": "Distinct resume verified. Interview scheduled Jun 2, 2:00 PM IST.",
    },
    {
        "name": "Pooja Malhotra",
        "email": "pooja.m@gmail.com",
        "phone": "9988776655",
        "experience_years": 3.0,
        "current_salary": 35000.0,
        "expected_salary": 45000.0,
        "notice_period_days": 15,
        "location": "Surat",
        "skills": [
            "Adobe Photoshop", "Adobe Illustrator", "Branding",
            "Typography", "UI", "Motion Graphics",
        ],
        "current_role": "Graphic Designer",
        "source": CandidateSource.MANUAL,
        "duplicate_flag": False,
        "notes": "Distinct resume verified. Interview scheduled Jun 3, 2:00 PM IST.",
    },
    # ── Duplicate resume group (8 candidates, identical text confirmed) ──────
    {
        "name": "Aarav Mehta",
        "email": "aarav.mehta@gmail.com",
        "phone": "9876543210",
        "experience_years": 5.0,
        "current_salary": 45000.0,
        "expected_salary": 55000.0,
        "notice_period_days": 30,
        "location": "Surat",
        "skills": [],
        "current_role": "Graphic Designer",
        "source": CandidateSource.NAUKRI,
        "duplicate_flag": True,
        "notes": (
            "DUPLICATE RESUME RISK: Resume text identical to 7 other applicants. "
            "Employer: Creative Studio Pvt Ltd (2019–present). "
            "Do NOT shortlist without direct verification."
        ),
    },
    {
        "name": "Aditya Verma",
        "email": "aditya.verma@gmail.com",
        "phone": "9871209876",
        "experience_years": 6.0,
        "current_salary": 55000.0,
        "expected_salary": 65000.0,
        "notice_period_days": 15,
        "location": "Surat",
        "skills": [],
        "current_role": "Graphic Designer",
        "source": CandidateSource.NAUKRI,
        "duplicate_flag": True,
        "notes": (
            "DUPLICATE RESUME RISK: Resume text identical to 7 other applicants. "
            "Employer: Creative Studio Pvt Ltd (2019–present). "
            "Do NOT shortlist without direct verification."
        ),
    },
    {
        "name": "Kunal Patel",
        "email": "kunal.patel@gmail.com",
        "phone": "9898123456",
        "experience_years": 1.0,
        "current_salary": 35000.0,
        "expected_salary": 40000.0,
        "notice_period_days": 15,
        "location": "Surat",
        "skills": [],
        "current_role": "Graphic Designer",
        "source": CandidateSource.NAUKRI,
        "duplicate_flag": True,
        "notes": (
            "DUPLICATE RESUME RISK: Resume text identical to 7 other applicants. "
            "Under-qualified (1 yr vs 3 yr minimum). "
            "Employer: Creative Studio Pvt Ltd (2019–present)."
        ),
    },
    {
        "name": "Neha Joshi",
        "email": "neha.joshi@gmail.com",
        "phone": "9812345678",
        "experience_years": 6.0,
        "current_salary": 65000.0,
        "expected_salary": 70000.0,
        "notice_period_days": 15,
        "location": "Surat",
        "skills": [],
        "current_role": "Graphic Designer",
        "source": CandidateSource.NAUKRI,
        "duplicate_flag": True,
        "notes": (
            "DUPLICATE RESUME RISK: Resume text identical to 7 other applicants. "
            "Expected salary ₹70,000 exceeds budget ₹50,000. "
            "Employer: Creative Studio Pvt Ltd (2019–present)."
        ),
    },
    {
        "name": "Rahul Nair",
        "email": "rahul.nair@gmail.com",
        "phone": "9887654321",
        "experience_years": 3.0,
        "current_salary": 45000.0,
        "expected_salary": 50000.0,
        "notice_period_days": 60,
        "location": "Surat",
        "skills": [],
        "current_role": "Graphic Designer",
        "source": CandidateSource.NAUKRI,
        "duplicate_flag": True,
        "notes": (
            "DUPLICATE RESUME RISK: Resume text identical to 7 other applicants. "
            "2-month notice period. "
            "Employer: Creative Studio Pvt Ltd (2019–present)."
        ),
    },
    {
        "name": "Riya Shah",
        "email": "riya.shah@gmail.com",
        "phone": "9825012345",
        "experience_years": 2.0,
        "current_salary": 35000.0,
        "expected_salary": 45000.0,
        "notice_period_days": 60,
        "location": "Surat",
        "skills": [],
        "current_role": "Graphic Designer",
        "source": CandidateSource.NAUKRI,
        "duplicate_flag": True,
        "notes": (
            "DUPLICATE RESUME RISK: Flagged in Master Sheet. "
            "Under-qualified (2 yrs vs 3 yr minimum). "
            "2-month notice period."
        ),
    },
    {
        "name": "Sneha Iyer",
        "email": "sneha.iyer@gmail.com",
        "phone": "9765432109",
        "experience_years": 6.0,
        "current_salary": 76000.0,
        "expected_salary": 80000.0,
        "notice_period_days": 30,
        "location": "Surat",
        "skills": [],
        "current_role": "Graphic Designer",
        "source": CandidateSource.NAUKRI,
        "duplicate_flag": True,
        "notes": (
            "DUPLICATE RESUME RISK: Resume text identical to 7 other applicants. "
            "Expected salary ₹80,000 far exceeds budget ₹50,000. "
            "Employer: Creative Studio Pvt Ltd (2019–present)."
        ),
    },
    {
        "name": "Vikram Desai",
        "email": "vikram.desai@gmail.com",
        "phone": "9909012345",
        "experience_years": 6.0,
        "current_salary": 87000.0,
        "expected_salary": 90000.0,
        "notice_period_days": 15,
        "location": "Surat",
        "skills": [],
        "current_role": "Graphic Designer",
        "source": CandidateSource.NAUKRI,
        "duplicate_flag": True,
        "notes": (
            "DUPLICATE RESUME RISK: Resume text identical to 7 other applicants. "
            "Expected salary ₹90,000 far exceeds budget ₹50,000. "
            "Male candidate (role prefers female). "
            "Employer: Creative Studio Pvt Ltd (2019–present)."
        ),
    },
]


# ── Design Engineer CAD Candidates (CAD Crowd — June 2–6 screening schedule) ─
# Skills and experience from CLAUDE.md calendar entries (real CAD Crowd data).
# No email addresses available from the CAD Crowd source.

CAD_CANDIDATES = [
    {
        "name": "Abhyuday C.",
        "email": None,
        "phone": None,
        "experience_years": 1.5,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["Fusion 360", "AutoCAD", "SolidWorks", "Blender"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com/profile/166710-abhyudayc1",
        "notes": "CAD Crowd. Screening call Jun 2, 10:00 AM IST. External score 92/100.",
    },
    {
        "name": "Akshat J.",
        "email": None,
        "phone": None,
        "experience_years": 2.0,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["Fusion 360", "SolidWorks", "AutoCAD", "ANSYS"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": "CAD Crowd. Screening call Jun 2, 11:00 AM IST. External score 90/100.",
    },
    {
        "name": "Tirth B.",
        "email": None,
        "phone": None,
        "experience_years": 1.5,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["Fusion 360", "SolidWorks", "AutoCAD", "DFM"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": "CAD Crowd. ⭐ 5.0 top-rated. Screening call Jun 3, 10:00 AM IST. External score 88/100.",
    },
    {
        "name": "Dharmin M.",
        "email": None,
        "phone": None,
        "experience_years": 2.5,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["Fusion 360", "SolidWorks", "Creo", "AutoCAD"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": "CAD Crowd. NPD/R&D background. Screening call Jun 3, 11:00 AM IST. External score 87/100.",
    },
    {
        "name": "Gadhiya P.",
        "email": None,
        "phone": None,
        "experience_years": 2.0,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["SolidWorks", "AutoCAD"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": "CAD Crowd. Screening call Jun 4, 10:00 AM IST. External score 78/100.",
    },
    {
        "name": "Sanidhya T.",
        "email": None,
        "phone": None,
        "experience_years": 2.0,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["SolidWorks", "CATIA", "NX", "Creo", "AutoCAD"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": (
            "CAD Crowd. Tool design + GD&T. B.E. Mech GTU 2020. "
            "Post Diploma Indo German Tool Room. "
            "Screening call Jun 4, 11:00 AM IST. External score 75/100."
        ),
    },
    {
        "name": "Kajal B.",
        "email": None,
        "phone": None,
        "experience_years": 2.0,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["SolidWorks", "CATIA", "Creo"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": "CAD Crowd. R&D background. Screening call Jun 5, 10:00 AM IST. External score 75/100.",
    },
    {
        "name": "Hemal Design Works",
        "email": None,
        "phone": None,
        "experience_years": 2.0,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["SolidWorks", "Creo", "NX", "Mastercam"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": "CAD Crowd. Product design + CNC machining. Screening call Jun 5, 11:00 AM IST. External score 70/100.",
    },
    {
        "name": "Hitesh K.",
        "email": None,
        "phone": None,
        "experience_years": 0.5,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["AutoCAD", "SolidWorks"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": (
            "CAD Crowd. Fresher — B.E. Mech SVIT Vasad 2021, CGPA 7.83. "
            "Screening call Jun 6, 10:00 AM IST. External score 65/100."
        ),
    },
    {
        "name": "Manish P.",
        "email": None,
        "phone": None,
        "experience_years": 0.0,
        "current_salary": None,
        "expected_salary": None,
        "notice_period_days": None,
        "location": None,
        "skills": ["SolidWorks", "AutoCAD"],
        "current_role": "Design Engineer",
        "source": CandidateSource.MANUAL,
        "source_ref": "cadcrowd.com",
        "notes": (
            "CAD Crowd. BACKUP. SolidWorks certified fresher. "
            "Screening call Jun 6, 11:00 AM IST. External score 62/100."
        ),
    },
]


# ── Seeding Logic ─────────────────────────────────────────────────────────────

async def seed():
    print("Initialising database…")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Clear existing data so re-runs are idempotent
        await session.execute(delete(ShortlistEntry))
        await session.execute(delete(Candidate))
        await session.execute(delete(Job))
        await session.commit()
        print("Cleared existing data.")

        # ── Insert Jobs ─────────────────────────────────────────────────────
        job_objects: list[Job] = []
        for jd in JOBS:
            job = Job(
                title=jd["title"],
                company=jd["company"],
                location=jd["location"],
                description=jd["description"],
                skills=jd["skills"],
                experience_min=jd["experience_min"],
                experience_max=jd["experience_max"],
                salary_min=jd["salary_min"],
                salary_max=jd["salary_max"],
                job_type=jd["job_type"],
                status=jd["status"],
            )
            session.add(job)
        await session.commit()

        # Re-fetch to get IDs
        from sqlalchemy import select
        result = await session.execute(select(Job).order_by(Job.id))
        job_objects = list(result.scalars().all())
        job_gd = job_objects[0]   # Sr. Graphic Designer
        job_cad = job_objects[1]  # Design Engineer CAD
        print(f"Created jobs: [{job_gd.id}] {job_gd.title}, [{job_cad.id}] {job_cad.title}")

        # ── Insert Graphic Designer Candidates & Score ───────────────────────
        print("\nSeeding Graphic Designer candidates…")
        for cdata in GD_CANDIDATES:
            cand = Candidate(
                name=cdata["name"],
                email=cdata.get("email"),
                phone=cdata.get("phone"),
                skills=cdata["skills"],
                experience_years=cdata.get("experience_years"),
                current_salary=cdata.get("current_salary"),
                expected_salary=cdata.get("expected_salary"),
                notice_period_days=cdata.get("notice_period_days"),
                location=cdata.get("location"),
                current_role=cdata.get("current_role"),
                source=cdata["source"],
                source_ref=cdata.get("source_ref"),
            )
            session.add(cand)
            await session.flush()  # get ID before creating shortlist entry

            result = score_candidate(
                candidate_skills=cand.skills or [],
                candidate_experience=cand.experience_years,
                candidate_expected_salary=cand.expected_salary,
                candidate_location=cand.location,
                candidate_role=cand.current_role,
                job_title=job_gd.title,
                job_skills=job_gd.skills or [],
                job_experience_min=job_gd.experience_min,
                job_experience_max=job_gd.experience_max,
                job_salary_min=job_gd.salary_min,
                job_salary_max=job_gd.salary_max,
                job_location=job_gd.location,
            )

            # Duplicates are rejected regardless of score
            if cdata["duplicate_flag"]:
                status = ShortlistStatus.REJECTED
            elif cdata["name"] in ("Kavya Rao", "Pooja Malhotra"):
                status = ShortlistStatus.INTERVIEW_SCHEDULED
            elif result.decision == "AUTO_SHORTLIST":
                status = ShortlistStatus.SHORTLISTED
            elif result.decision == "MANUAL_REVIEW":
                status = ShortlistStatus.PENDING
            else:
                status = ShortlistStatus.REJECTED

            shortlist = ShortlistEntry(
                job_id=job_gd.id,
                candidate_id=cand.id,
                score=result.total,
                score_breakdown=result.breakdown,
                status=status,
                recruiter_notes=cdata.get("notes"),
            )
            session.add(shortlist)
            flag = " ⚠ DUPLICATE" if cdata["duplicate_flag"] else ""
            print(
                f"  {cand.name:25s} score={result.total:5.1f}  "
                f"decision={result.decision:15s}  status={status.value}{flag}"
            )

        await session.commit()

        # ── Insert CAD Candidates & Score ────────────────────────────────────
        print("\nSeeding Design Engineer (CAD) candidates…")
        for cdata in CAD_CANDIDATES:
            cand = Candidate(
                name=cdata["name"],
                email=cdata.get("email"),
                phone=cdata.get("phone"),
                skills=cdata["skills"],
                experience_years=cdata.get("experience_years"),
                current_salary=cdata.get("current_salary"),
                expected_salary=cdata.get("expected_salary"),
                notice_period_days=cdata.get("notice_period_days"),
                location=cdata.get("location"),
                current_role=cdata.get("current_role"),
                source=cdata["source"],
                source_ref=cdata.get("source_ref"),
            )
            session.add(cand)
            await session.flush()

            result = score_candidate(
                candidate_skills=cand.skills or [],
                candidate_experience=cand.experience_years,
                candidate_expected_salary=cand.expected_salary,
                candidate_location=cand.location,
                candidate_role=cand.current_role,
                job_title=job_cad.title,
                job_skills=job_cad.skills or [],
                job_experience_min=job_cad.experience_min,
                job_experience_max=job_cad.experience_max,
                job_salary_min=job_cad.salary_min,
                job_salary_max=job_cad.salary_max,
                job_location=job_cad.location,
            )

            if cdata["name"] in ("Abhyuday C.", "Akshat J.", "Tirth B.", "Dharmin M."):
                status = ShortlistStatus.INTERVIEW_SCHEDULED
            elif result.decision == "AUTO_SHORTLIST":
                status = ShortlistStatus.SHORTLISTED
            elif result.decision == "MANUAL_REVIEW":
                status = ShortlistStatus.PENDING
            else:
                status = ShortlistStatus.REJECTED

            shortlist = ShortlistEntry(
                job_id=job_cad.id,
                candidate_id=cand.id,
                score=result.total,
                score_breakdown=result.breakdown,
                status=status,
                recruiter_notes=cdata.get("notes"),
            )
            session.add(shortlist)
            print(
                f"  {cand.name:25s} score={result.total:5.1f}  "
                f"decision={result.decision:15s}  status={status.value}"
            )

        await session.commit()

    print("\n✓ Seed complete.")
    print(f"  Jobs:        {len(JOBS)}")
    print(f"  GD cands:    {len(GD_CANDIDATES)}")
    print(f"  CAD cands:   {len(CAD_CANDIDATES)}")
    print(f"  Total cands: {len(GD_CANDIDATES) + len(CAD_CANDIDATES)}")


if __name__ == "__main__":
    asyncio.run(seed())
