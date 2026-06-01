#!/usr/bin/env python3
"""
Standalone Apify candidate sourcing script.

Usage:
  APIFY_API_TOKEN=apify_xxx python scripts/source_candidates.py --job-id 3

Runs LinkedIn + Naukri scrapers via Apify, scores candidates against the job,
prints a shortlist table, and (optionally) writes results to the DB.

Options:
  --job-id INT    Job DB ID to source for (default: 3 = HR Executive)
  --dry-run       Print results without writing to DB
  --linkedin-only Skip Naukri
  --limit INT     Max candidates per platform (default: 30)
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apify_client import ApifyClient


LINKEDIN_ACTOR = "get-leads/linkedin-scraper"
NAUKRI_ACTOR   = "makework36/naukri-scraper"


def run_linkedin(token: str, query: str, location: str, limit: int) -> list[dict]:
    print(f"[LinkedIn] Searching: '{query}' in '{location}' (max {limit})")
    client = ApifyClient(token)
    run = client.actor(LINKEDIN_ACTOR).call(run_input={
        "mode": "search_profiles",
        "searchQuery": query,
        "location": location,
        "maxResults": limit,
        "discoverEmails": True,
    }, timeout_secs=120)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    print(f"[LinkedIn] → {len(items)} profiles returned")
    return items


def run_naukri(token: str, keyword: str, location: str, limit: int) -> list[dict]:
    print(f"[Naukri]   Searching: '{keyword}' in '{location}' (max {limit})")
    client = ApifyClient(token)
    run = client.actor(NAUKRI_ACTOR).call(run_input={
        "keyword": keyword,
        "location": location,
        "maxResults": limit,
    }, timeout_secs=120)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    print(f"[Naukri]   → {len(items)} listings returned")
    return items


def score_simple(profile: dict, required_skills: list[str]) -> int:
    """Quick relevance score 0-100 for display purposes."""
    score = 0
    text = str(profile).lower()
    # Skill match
    for skill in required_skills:
        if skill.lower() in text:
            score += 10
    # HR-specific keywords
    for kw in ["payroll", "recruitment", "employee relations", "leave management", "onboarding", "hr"]:
        if kw in text:
            score += 5
    # Surat location
    if "surat" in text or "gujarat" in text:
        score += 10
    # Experience range
    import re
    m = re.search(r"(\d+)\s*(?:yr|year)", text)
    if m:
        yrs = int(m.group(1))
        if 1 <= yrs <= 3:
            score += 15
        elif 3 < yrs <= 5:
            score += 8
    return min(score, 100)


def print_table(candidates: list[dict], platform: str, required_skills: list[str]):
    print(f"\n{'━'*80}")
    print(f"  {platform} Results")
    print(f"{'━'*80}")
    print(f"{'#':<3} {'Name':<28} {'Role/Title':<30} {'Score':>5} {'Contact':<25}")
    print(f"{'─'*80}")
    for i, c in enumerate(candidates, 1):
        name = (c.get("name") or c.get("jobTitle") or "—")[:27]
        role = (c.get("headline") or c.get("current_role") or c.get("jobTitle") or "—")[:29]
        score = score_simple(c, required_skills)
        contact = c.get("email") or c.get("linkedinUrl") or c.get("url") or c.get("jobUrl") or "—"
        contact = contact[:24] if contact else "—"
        print(f"{i:<3} {name:<28} {role:<30} {score:>5} {contact:<25}")
    print(f"{'━'*80}\n")


def main():
    parser = argparse.ArgumentParser(description="Apify candidate sourcing")
    parser.add_argument("--job-id", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--linkedin-only", action="store_true")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        print("ERROR: Set APIFY_API_TOKEN environment variable")
        print("  export APIFY_API_TOKEN=apify_xxxxxxxxxxxx")
        print("  Get your token from: https://console.apify.com/account/integrations")
        sys.exit(1)

    # HR Executive role profile
    JOB_QUERIES = {
        3: {
            "title": "HR Executive",
            "linkedin_queries": ["HR Executive payroll Surat", "HR Generalist Surat Gujarat"],
            "naukri_keyword": "HR Executive",
            "location": "Surat, Gujarat",
            "required_skills": ["Payroll", "Recruitment", "Employee Relations", "Leave Management",
                                 "Onboarding", "HR Policies", "Compliance"],
        }
    }

    job_cfg = JOB_QUERIES.get(args.job_id, JOB_QUERIES[3])
    print(f"\n{'='*80}")
    print(f"  Apify Sourcing — {job_cfg['title']} | Job ID {args.job_id}")
    print(f"  Location: {job_cfg['location']} | Limit: {args.limit} per platform")
    print(f"{'='*80}\n")

    all_results = []

    # LinkedIn searches
    for query in job_cfg["linkedin_queries"]:
        try:
            items = run_linkedin(token, query, job_cfg["location"], args.limit)
            all_results.extend([{**i, "_platform": "LinkedIn"} for i in items])
        except Exception as e:
            print(f"[LinkedIn] ERROR: {e}")

    if all_results:
        linkedin_items = [r for r in all_results if r.get("_platform") == "LinkedIn"]
        linkedin_items.sort(key=lambda x: score_simple(x, job_cfg["required_skills"]), reverse=True)
        print_table(linkedin_items[:20], "LinkedIn Profiles", job_cfg["required_skills"])

    # Naukri search
    if not args.linkedin_only:
        try:
            naukri_items = run_naukri(token, job_cfg["naukri_keyword"], job_cfg["location"], args.limit)
            naukri_items.sort(key=lambda x: score_simple(x, job_cfg["required_skills"]), reverse=True)
            all_results.extend([{**i, "_platform": "Naukri"} for i in naukri_items])
            print_table(naukri_items[:20], "Naukri Listings", job_cfg["required_skills"])
        except Exception as e:
            print(f"[Naukri] ERROR: {e}")

    # Summary
    total = len(all_results)
    high_score = [r for r in all_results if score_simple(r, job_cfg["required_skills"]) >= 50]
    print(f"\n{'='*80}")
    print(f"  SUMMARY — Total: {total} results | High-match (≥50): {len(high_score)}")
    print(f"{'='*80}")
    print("\nTop candidates to contact:")
    all_results.sort(key=lambda x: score_simple(x, job_cfg["required_skills"]), reverse=True)
    for i, r in enumerate(all_results[:10], 1):
        name = r.get("name") or r.get("jobTitle") or "—"
        contact = r.get("email") or r.get("url") or r.get("linkedinUrl") or "—"
        score = score_simple(r, job_cfg["required_skills"])
        print(f"  {i}. {name:<30} Score: {score:>3}  Contact: {contact}")

    if not args.dry_run and total > 0:
        print(f"\nTo import these {total} candidates into the DB, run:")
        print(f"  POST https://kgirdharlal-recruitment.vercel.app/api/v1/sourcing/{args.job_id}")
        print("  (with APIFY_API_TOKEN + USE_MOCK_ADAPTERS=false set in Vercel env)")

    print()


if __name__ == "__main__":
    main()
