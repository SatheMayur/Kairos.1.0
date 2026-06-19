"""Tests for the phone normalizer and the WhatsApp send / CSV-import phone path.

Covers the messy real-world Indian CSV phone formats that the APPLICANTS →
auto-WhatsApp pipeline must handle:
  - plain 10-digit, +91 / spaces / dashes, leading trunk 0, 91 country code
  - the Excel ".0" float artifact (numbers turned into floats on export)
  - multiple numbers in one cell ("9876543210 / 9123456789")
  - junk ("NA", "Not Available", "-", landlines, <10 digits) → None / skipped
"""
import pytest
import pytest_asyncio

from app.utils.phone import (
    normalize_indian_mobile,
    is_valid_mobile,
    to_chat_id,
    to_local_10_or_none,
    norm_phone,
)


# ── The core normalizer ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    # plain
    ("9876543210", "9876543210"),
    # +91 with spaces
    ("+91 98765 43210", "9876543210"),
    # +91 with dash
    ("+91-9876543210", "9876543210"),
    # leading trunk 0 + space
    ("0 9876543210", "9876543210"),
    ("09876543210", "9876543210"),
    # bare country code, no +
    ("919876543210", "9876543210"),
    ("+919876543210", "9876543210"),
    ("0919876543210", "9876543210"),
    # internal space only
    ("98765 43210", "9876543210"),
    # Excel ".0" float artifact — the classic export bug
    ("9876543210.0", "9876543210"),
    ("9876543210.00", "9876543210"),
    ("919876543210.0", "9876543210"),
    ("+91 98765 43210.0", "9876543210"),
    # multiple numbers in one cell → first valid wins
    ("9876543210 / 9123456789", "9876543210"),
    ("9876543210, 9123456789", "9876543210"),
    ("9876543210; 9123456789", "9876543210"),
    # tabs / surrounding whitespace
    ("  9876543210  ", "9876543210"),
    # all valid Indian mobile prefixes 6-9
    ("6012345678", "6012345678"),
    ("7012345678", "7012345678"),
    ("8012345678", "8012345678"),
])
def test_valid_numbers_normalize(raw, expected):
    assert normalize_indian_mobile(raw) == expected
    assert is_valid_mobile(raw) is True


@pytest.mark.parametrize("raw", [
    None, "", "   ",
    "NA", "na", "N/A", "Not Available", "not available",
    "-", "--", ".",
    "12345",            # too short
    "98765",            # too short
    "0123456789",       # 10 digits but starts 0 (not a mobile)
    "1234567890",       # starts 1 — not a mobile
    "5123456789",       # starts 5 — not a mobile
    "0612345678",       # landline-ish, 10 digits starting 0
    "022-12345678",     # Mumbai landline (STD code) — 10 digits but not a 6-9 mobile
    "98765432100",      # 11 digits, not a 91-prefixed mobile
    "abcdefghij",       # letters
    "98765abcde",       # mixed junk under 10 real digits
])
def test_junk_numbers_return_none(raw):
    assert normalize_indian_mobile(raw) is None
    assert is_valid_mobile(raw) is False


def test_multi_number_picks_first_valid_skipping_junk():
    # First token is junk/landline, second is a real mobile → return the mobile.
    assert normalize_indian_mobile("022-12345678 / 9876543210") == "9876543210"
    assert normalize_indian_mobile("NA / 9876543210") == "9876543210"


def test_int_and_float_inputs():
    # Excel may hand us an int or float directly (not a string).
    assert normalize_indian_mobile(9876543210) == "9876543210"
    assert normalize_indian_mobile(9876543210.0) == "9876543210"


# ── to_chat_id: the WhatsApp JID for the bridge ──────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("9876543210", "919876543210@c.us"),
    ("+91 98765 43210", "919876543210@c.us"),
    ("+91-9876543210", "919876543210@c.us"),
    ("0 9876543210", "919876543210@c.us"),
    ("919876543210", "919876543210@c.us"),
    ("9876543210.0", "919876543210@c.us"),        # Excel artifact
    ("9876543210 / 9123456789", "919876543210@c.us"),
])
def test_to_chat_id_valid(raw, expected):
    assert to_chat_id(raw) == expected


@pytest.mark.parametrize("raw", ["NA", "", None, "-", "12345", "0612345678", "98765432100"])
def test_to_chat_id_junk_is_none(raw):
    assert to_chat_id(raw) is None


# ── norm_phone (dedup key) agrees with the send path ─────────────────────────

def test_norm_phone_collapses_formats_for_dedup():
    forms = ["9876543210", "+91 98765 43210", "+91-9876543210",
             "0 9876543210", "919876543210", "9876543210.0",
             "98765 43210", "9876543210 / 9123456789"]
    keys = {norm_phone(f) for f in forms}
    assert keys == {"9876543210"}


def test_norm_phone_junk_is_empty():
    for raw in ("NA", "", None, "-", "12345"):
        assert norm_phone(raw) == ""


def test_to_local_10_or_none_is_strict():
    assert to_local_10_or_none("+91 98765 43210") == "9876543210"
    assert to_local_10_or_none("NA") is None
    assert to_local_10_or_none("0612345678") is None


# ── CSV adapters normalize phones at parse time ──────────────────────────────

def test_naukri_adapter_normalizes_and_skips_junk():
    from app.adapters.naukri import NaukriCSVAdapter
    csv_text = (
        "Candidate Name,Email ID,Mobile Number\n"
        "Asha,asha@x.com,+91 98765 43210\n"          # messy but valid
        "Bharat,bharat@x.com,9876543210.0\n"          # Excel float
        "Junk Phone,junk@x.com,NA\n"                  # junk → None
        "Landline,land@x.com,022-12345678\n"          # landline → None
    )
    out = {c.name: c for c in NaukriCSVAdapter().parse_csv(csv_text)}
    assert out["Asha"].phone == "9876543210"
    assert out["Bharat"].phone == "9876543210"
    assert out["Junk Phone"].phone is None
    assert out["Landline"].phone is None


def test_workindia_adapter_normalizes_and_dedup_key_uses_clean_phone():
    from app.adapters.workindia import WorkIndiaCSVAdapter
    csv_text = (
        "Name,Mobile Number,Email\n"
        "Chetan,+91-9876543210,\n"        # no email → source_ref must use clean phone
        "Devi,98765 43210,\n"             # same person, different format
    )
    rows = WorkIndiaCSVAdapter().parse_csv(csv_text)
    refs = {r.name: r.source_ref for r in rows}
    # Both formats collapse to the same normalized phone in the dedup key.
    assert refs["Chetan"] == "workindia:9876543210"
    assert refs["Devi"] == "workindia:9876543210"


def test_apna_csv_adapter_normalizes_phone():
    from app.adapters.apna import ApnaCSVAdapter
    csv_text = (
        "Candidate Name,Mobile Number,Email\n"
        "Esha,919876543210,esha@x.com\n"
        "Faiz,Not Available,faiz@x.com\n"
    )
    out = {c.name: c for c in ApnaCSVAdapter().parse_csv(csv_text)}
    assert out["Esha"].phone == "9876543210"
    assert out["Esha"].whatsapp == "9876543210"
    assert out["Faiz"].phone is None


# ── WhatsApp send path: clean JID queued, junk skipped, bridge-offline safe ───

@pytest_asyncio.fixture
async def _wa_setup(db_session):
    return db_session


@pytest.mark.asyncio
async def test_send_whatsapp_queues_clean_chat_id(db_session):
    from app.services.whatsapp_openclaw import send_whatsapp
    from app.models.wa_queue import WAQueue
    from sqlalchemy import select

    # Bridge "offline" is the normal case here (no OPENCLAW_API_URL) — passing db
    # must QUEUE the message, never raise, never lose it.
    result = await send_whatsapp("+91 98765 43210", "Hi there", db=db_session)
    assert result and result.startswith("queued:")

    rows = (await db_session.execute(select(WAQueue))).scalars().all()
    assert len(rows) == 1
    # Stored as clean 91-prefixed digits; the Baileys bridge builds the JID.
    assert rows[0].phone == "919876543210"


@pytest.mark.asyncio
async def test_send_whatsapp_skips_junk_without_queueing(db_session):
    from app.services.whatsapp_openclaw import send_whatsapp
    from app.models.wa_queue import WAQueue
    from sqlalchemy import select

    for junk in ("NA", "", "12345", "0612345678"):
        result = await send_whatsapp(junk, "Hi", db=db_session)
        assert result is None, f"{junk!r} should not be sent"

    rows = (await db_session.execute(select(WAQueue))).scalars().all()
    assert rows == []  # nothing garbage queued


@pytest.mark.asyncio
async def test_send_whatsapp_handles_excel_float(db_session):
    from app.services.whatsapp_openclaw import send_whatsapp
    from app.models.wa_queue import WAQueue
    from sqlalchemy import select

    result = await send_whatsapp("9876543210.0", "Hi", db=db_session)
    assert result and result.startswith("queued:")
    rows = (await db_session.execute(select(WAQueue))).scalars().all()
    assert rows[0].phone == "919876543210"


# ── outreach._resolve_channel: junk phone → not WhatsApp ─────────────────────

def test_resolve_channel_rejects_junk_phone():
    from app.services.outreach import _resolve_channel
    from app.models.candidate import Candidate, CandidateSource
    from app.models.outreach import OutreachChannel

    # Junk phone, no email → UNREACHABLE (surfaced in Needs Fixing, not messaged).
    c = Candidate(name="X", phone="NA", whatsapp="NA", source=CandidateSource.NAUKRI)
    ch, _ = _resolve_channel(c, OutreachChannel.WHATSAPP)
    assert ch == OutreachChannel.UNREACHABLE

    # Junk phone but valid email → fall back to EMAIL.
    c2 = Candidate(name="Y", phone="NA", email="y@x.com", source=CandidateSource.NAUKRI)
    ch2, recipient2 = _resolve_channel(c2, OutreachChannel.WHATSAPP)
    assert ch2 == OutreachChannel.EMAIL and recipient2 == "y@x.com"

    # Valid messy phone → WhatsApp to the clean 10-digit number.
    c3 = Candidate(name="Z", phone="+91 98765 43210", source=CandidateSource.NAUKRI)
    ch3, recipient3 = _resolve_channel(c3, OutreachChannel.WHATSAPP)
    assert ch3 == OutreachChannel.WHATSAPP and recipient3 == "9876543210"


# ── data_quality flags junk phones ───────────────────────────────────────────

def test_data_quality_flags_junk_phone():
    from app.services.data_quality import analyze_candidate
    from app.models.candidate import Candidate, CandidateSource

    # Landline, no email → high-severity contact problem.
    c = Candidate(id=1, name="L", phone="022-12345678", source=CandidateSource.NAUKRI)
    codes = {i["code"] for i in analyze_candidate(c)}
    assert "BAD_PHONE" in codes or "SHORT_PHONE" in codes

    # Clean valid mobile → no phone issue.
    c2 = Candidate(id=2, name="OK", phone="9876543210", email="ok@x.com",
                   skills=["x"], source=CandidateSource.NAUKRI)
    codes2 = {i["code"] for i in analyze_candidate(c2)}
    assert "BAD_PHONE" not in codes2 and "SHORT_PHONE" not in codes2


# ── full import pipeline: no crash, dedup (no double-message), clean queue ───

@pytest_asyncio.fixture
async def _job(db_session):
    from app.models.job import Job, JobStatus
    j = Job(title="Operator", company="K. Girdharlal", location="Surat",
            status=JobStatus.ACTIVE)
    db_session.add(j)
    await db_session.flush()
    return j


@pytest.mark.asyncio
async def test_import_with_messy_and_junk_phones_does_not_crash(_job, db_session):
    """One bad phone/candidate must never abort the whole upload, and valid
    messy phones become clean WhatsApp queue entries."""
    from app.adapters.naukri import NaukriCSVAdapter
    from app.api.import_csv import _run_import_pipeline
    from app.models.wa_queue import WAQueue
    from sqlalchemy import select

    csv_text = (
        "Candidate Name,Email ID,Mobile Number,Key Skills\n"
        "Good One,good@x.com,+91 98765 43210,welding\n"     # valid messy phone
        "Excel Float,float@x.com,9123456789.0,welding\n"    # Excel ".0"
        "Junk Phone,junk@x.com,Not Available,welding\n"     # junk, but has email
        "No Skills,,9988776655,\n"                          # missing name guard etc.
    )
    raws = NaukriCSVAdapter().parse_csv(csv_text)
    # The whole pipeline runs without raising even with junk/edge rows.
    result = await _run_import_pipeline(raws, _job.id, auto_outreach=True, db=db_session)
    assert result.total_parsed == len(raws)

    # Every queued WhatsApp number is clean 91-prefixed digits (no '+', spaces,
    # '.0', or junk strings).
    rows = (await db_session.execute(select(WAQueue))).scalars().all()
    for r in rows:
        assert r.phone.isdigit() and r.phone.startswith("91") and len(r.phone) == 12


@pytest.mark.asyncio
async def test_reupload_does_not_double_message(_job, db_session):
    """Re-uploading the same applicants must skip duplicates and queue no new
    WhatsApp messages for people already contacted."""
    from app.adapters.naukri import NaukriCSVAdapter
    from app.api.import_csv import _run_import_pipeline
    from app.models.wa_queue import WAQueue
    from sqlalchemy import select, func

    csv_text = (
        "Candidate Name,Email ID,Mobile Number,Key Skills\n"
        "Repeat Person,repeat@x.com,9876543210,welding\n"
    )
    raws = NaukriCSVAdapter().parse_csv(csv_text)

    r1 = await _run_import_pipeline(raws, _job.id, auto_outreach=True, db=db_session)
    count1 = (await db_session.execute(select(func.count()).select_from(WAQueue))).scalar()

    # Second upload of the very same file — must be detected as a duplicate.
    raws2 = NaukriCSVAdapter().parse_csv(csv_text)
    r2 = await _run_import_pipeline(raws2, _job.id, auto_outreach=True, db=db_session)
    count2 = (await db_session.execute(select(func.count()).select_from(WAQueue))).scalar()

    assert r2.duplicates_skipped == 1
    assert r2.outreach_queued == 0
    assert count2 == count1  # no new WhatsApp messages queued on re-upload


@pytest.mark.asyncio
async def test_bridge_offline_queues_instead_of_losing_candidate(_job, db_session):
    """With the bridge offline (no WAHA url), a valid candidate's message must be
    QUEUED (WAQueue) for later delivery — never dropped or raised."""
    from app.models.candidate import Candidate, CandidateSource
    from app.models.outreach import OutreachChannel, OutreachType, OutreachStatus
    from app.services.outreach import send_outreach
    from app.models.wa_queue import WAQueue
    from sqlalchemy import select

    c = Candidate(name="Queued Person", phone="+91-9876543210",
                  whatsapp="+91-9876543210", source=CandidateSource.NAUKRI)
    db_session.add(c)
    await db_session.flush()

    log = await send_outreach(
        candidate=c, job=_job,
        channel=OutreachChannel.WHATSAPP,
        outreach_type=OutreachType.INITIAL_CONTACT,
        db=db_session,
    )
    assert log.status == OutreachStatus.SENT          # queued counts as accepted
    assert log.channel == OutreachChannel.WHATSAPP
    rows = (await db_session.execute(select(WAQueue))).scalars().all()
    assert len(rows) == 1 and rows[0].phone == "919876543210"
