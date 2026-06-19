"""Duplicate & resume-integrity detection.

Three independent signals, surfaced separately because they mean different things:

  • same_contact — candidates that share an email address or phone number.
                   Almost always the SAME person who applied more than once.
                   Action: review and keep one record.

                   This also catches the "two-path Apna" case: the same person
                   imported once from a CSV/Excel upload (which carries an email
                   or phone) and once from an Apna live-search (which often has
                   NO email and NO phone because they are locked). Those two rows
                   share neither contact detail nor source_ref, so we additionally
                   match on a STRONG name-plus-context signal (see below). These
                   are only ever SURFACED for the owner to merge by hand — never
                   merged automatically.

  • same_resume  — candidates whose resume text is identical or near-identical.
                   Often DIFFERENT people submitting copy-pasted resumes
                   (a known fraud pattern). Action: verify before shortlisting/offering.

Pure functions, no DB access — easy to unit-test.
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.models.candidate import Candidate
from app.utils.phone import norm_phone

_WS = re.compile(r"\s+")
_NON = re.compile(r"[^a-z0-9 ]+")


def _norm_text(text: str | None) -> str:
    if not text:
        return ""
    t = _NON.sub(" ", text.lower())
    return _WS.sub(" ", t).strip()


def _resume_signal(c: Candidate) -> str:
    """Best available text fingerprint for a candidate.

    Prefer the full resume text; fall back to a composite of structured fields
    so copy-paste is still detectable even when raw resume text is missing.
    """
    if c.raw_profile and len(c.raw_profile.strip()) >= 40:
        return _norm_text(c.raw_profile)
    parts = [
        c.current_role or "",
        c.current_employer or "",
        c.education or "",
        " ".join(sorted(c.skills or [])),
    ]
    composite = _norm_text(" ".join(parts))
    return composite if len(composite) >= 20 else ""


def _norm_phone(phone: str | None) -> str:
    return norm_phone(phone)


def _norm_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _norm_name(name: str | None) -> str:
    """Normalised full name for matching: lowercase, punctuation stripped,
    word order preserved. Returns '' for blanks so it can never match."""
    return _norm_text(name)


# Single-token names ("Raj", "Priya") are far too common to be a reliable
# person identifier across two different sources, so we only treat a name as a
# strong signal when it has at least this many word parts (i.e. first + last).
_MIN_NAME_PARTS = 2


def _norm_field(value: str | None) -> str:
    """Normalised employer / location text for matching."""
    return _norm_text(value)


def _shingles(text: str, k: int = 5) -> set[str]:
    words = text.split()
    if len(words) < k:
        return set(words)
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _cand_brief(c: Candidate) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "phone": c.phone or c.whatsapp,
        "current_employer": c.current_employer,
        "current_role": c.current_role,
        "location": c.location,
        "source": c.source.value if c.source else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def find_duplicates(candidates: list[Candidate], near_threshold: float = 0.82) -> dict:
    """Group candidates into duplicate clusters. See module docstring."""
    id_to_cand = {c.id: c for c in candidates}

    # ── 1. Same contact (email / phone) ──────────────────────────────────────
    by_email: dict[str, list] = defaultdict(list)
    by_phone: dict[str, list] = defaultdict(list)
    for c in candidates:
        e = _norm_email(c.email)
        p = _norm_phone(c.phone or c.whatsapp)
        if e:
            by_email[e].append(c)
        if p:
            by_phone[p].append(c)

    contact_uf = _UnionFind()
    contact_members: set[int] = set()
    for group in list(by_email.values()) + list(by_phone.values()):
        if len(group) < 2:
            continue
        ids = [c.id for c in group]
        for other in ids[1:]:
            contact_uf.union(ids[0], other)
        contact_members.update(ids)

    # ── 1b. Likely same person across sources (e.g. CSV import + Apna live-search) ─
    #
    # The two-path Apna case: a candidate uploaded from a spreadsheet carries an
    # email/phone, while the same candidate pulled from Apna live-search has them
    # locked (blank). So they share neither contact detail nor source_ref and the
    # checks above miss them entirely.
    #
    # We close that gap with a deliberately CONSERVATIVE rule. A shared name is
    # NECESSARY but NOT SUFFICIENT — names repeat too often to merge people on a
    # name alone. We only link two records when their full names match AND they
    # also agree on a second, independent piece of context:
    #
    #     same normalised full name (>= 2 word parts)
    #         AND ( same phone  OR  same current employer  OR  same location )
    #
    # Requiring that extra agreement is what keeps two genuinely different people
    # who merely happen to share a name from ever being grouped together.
    by_name: dict[str, list] = defaultdict(list)
    for c in candidates:
        nm = _norm_name(c.name)
        if nm and len(nm.split()) >= _MIN_NAME_PARTS:
            by_name[nm].append(c)

    name_link_reasons: dict[frozenset[int], str] = {}
    for group in by_name.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            ci = group[i]
            for j in range(i + 1, len(group)):
                cj = group[j]
                shared = []
                pi, pj = _norm_phone(ci.phone or ci.whatsapp), _norm_phone(cj.phone or cj.whatsapp)
                if pi and pi == pj:
                    shared.append("phone number")
                ei, ej = _norm_field(ci.current_employer), _norm_field(cj.current_employer)
                if ei and ei == ej:
                    shared.append("employer")
                li, lj = _norm_field(ci.location), _norm_field(cj.location)
                if li and li == lj:
                    shared.append("location")
                if not shared:
                    continue  # name alone is never enough
                contact_uf.union(ci.id, cj.id)
                contact_members.add(ci.id)
                contact_members.add(cj.id)
                key = frozenset((ci.id, cj.id))
                name_link_reasons[key] = ", ".join(shared)

    contact_map: dict[int, list] = defaultdict(list)
    for cid in contact_members:
        contact_map[contact_uf.find(cid)].append(id_to_cand[cid])

    same_contact = []
    for members in contact_map.values():
        if len(members) < 2:
            continue
        email_list = [_norm_email(m.email) for m in members if _norm_email(m.email)]
        phone_list = [_norm_phone(m.phone or m.whatsapp)
                      for m in members if _norm_phone(m.phone or m.whatsapp)]
        emails = set(email_list)
        phones = set(phone_list)
        reasons = []
        # Only call it a shared email/phone when at least two records actually
        # carry that same value — otherwise a cluster linked by name+context
        # (where just one record has an email) would be mislabelled.
        if len(emails) == 1 and len(email_list) >= 2:
            reasons.append("same email address")
        if len(phones) == 1 and len(phone_list) >= 2:
            reasons.append("same phone number")

        # If this cluster was only linked by the name-plus-context rule (no shared
        # email and no shared phone), describe it in plain words for the owner.
        if not reasons:
            member_ids = frozenset(m.id for m in members)
            ctx = ""
            for key, why in name_link_reasons.items():
                if key <= member_ids:
                    ctx = why
                    break
            names = {_norm_name(m.name) for m in members}
            same_name = len(names) == 1
            if ctx and same_name:
                reasons.append(f"same name and {ctx}")
            elif ctx:
                reasons.append(f"matching name and {ctx}")

        same_contact.append({
            "kind": "same_contact",
            "match": " and ".join(reasons) or "shared contact details",
            "shared_email": email_list[0] if (len(emails) == 1 and len(email_list) >= 2) else None,
            "shared_phone": phone_list[0] if (len(phones) == 1 and len(phone_list) >= 2) else None,
            "candidates": sorted((_cand_brief(m) for m in members), key=lambda x: x["id"]),
        })

    # ── 2. Same / near-identical resume text ─────────────────────────────────
    signals = []
    for c in candidates:
        sig = _resume_signal(c)
        if sig and len(sig.split()) >= 8:
            signals.append((c, sig))

    resume_uf = _UnionFind()
    resume_members: set[int] = set()

    # 2a. Exact normalized text → instant cluster (word-for-word copy)
    by_hash: dict[str, list] = defaultdict(list)
    for c, sig in signals:
        by_hash[sig].append(c)
    for group in by_hash.values():
        if len(group) < 2:
            continue
        ids = [c.id for c in group]
        for other in ids[1:]:
            resume_uf.union(ids[0], other)
        resume_members.update(ids)

    # 2b. Near-duplicate via shingle Jaccard (bounded; skips already-linked pairs)
    if len(signals) <= 500:
        shing = [(c, _shingles(sig)) for c, sig in signals]
        for i in range(len(shing)):
            ci, si = shing[i]
            for j in range(i + 1, len(shing)):
                cj, sj = shing[j]
                if resume_uf.find(ci.id) == resume_uf.find(cj.id):
                    continue
                if _jaccard(si, sj) >= near_threshold:
                    resume_uf.union(ci.id, cj.id)
                    resume_members.add(ci.id)
                    resume_members.add(cj.id)

    resume_map: dict[int, list] = defaultdict(list)
    for cid in resume_members:
        resume_map[resume_uf.find(cid)].append(id_to_cand[cid])

    same_resume = []
    for members in resume_map.values():
        if len(members) < 2:
            continue
        sigs = {_resume_signal(m) for m in members}
        identical = len(sigs) == 1
        employers = {(m.current_employer or "").strip().lower()
                     for m in members if m.current_employer}
        same_resume.append({
            "kind": "same_resume",
            "match": "identical resume text" if identical else "near-identical resume text",
            "identical": identical,
            "shared_employer": next(iter(employers)) if len(employers) == 1 else None,
            "candidates": sorted((_cand_brief(m) for m in members), key=lambda x: x["id"]),
        })

    same_contact.sort(key=lambda cl: -len(cl["candidates"]))
    same_resume.sort(key=lambda cl: -len(cl["candidates"]))

    flagged: set[int] = set()
    for cl in same_contact + same_resume:
        for m in cl["candidates"]:
            flagged.add(m["id"])

    return {
        "same_contact": same_contact,
        "same_resume": same_resume,
        "summary": {
            "total_candidates": len(candidates),
            "contact_clusters": len(same_contact),
            "resume_clusters": len(same_resume),
            "flagged_candidates": len(flagged),
        },
    }
