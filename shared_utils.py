import re
from datetime import datetime, timezone, timedelta
from database import get_connection

def clean_text(value: str | None) -> str:
    if not value:
        return ""

    return (
        str(value)
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
        .strip()
    )

def normalize_job(raw: dict) -> dict:
    title = clean_text(raw.get("position") or raw.get("title") or raw.get("role"))
    company = clean_text(raw.get("company") or raw.get("company_name") or "")
    location = clean_text(raw.get("location") or raw.get("candidate_required_location") or "Remote")
    description = clean_text(raw.get("description") or raw.get("description_text") or "")
    url = clean_text(raw.get("url") or raw.get("apply_url") or raw.get("applyUrl") or "")
    salary = clean_text(raw.get("salary") or "")

    # Each source computes "how many hours old is this posting" differently
    # (career_sites.py: age_hours, naukri_playwright.py: hours_ago,
    # jobspy-sourced: age_hours via pipeline._jobspy_row_to_raw's date_posted
    # conversion) — normalized here to a single absolute posted_at timestamp
    # so it survives however long the row sits in the DB before being
    # rendered in a digest, rather than storing a relative "hours old" value
    # that would go stale the moment it's saved.
    age_hours = raw.get("age_hours")
    if age_hours is None:
        age_hours = raw.get("hours_ago")
    posted_at = None
    if age_hours is not None:
        posted_at = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()

    return {
        "title": title, "company": company, "location": location,
        "description": description, "url": url, "salary": salary,
        "posted_at": posted_at,
    }

def already_exists(url: str):
    conn = get_connection()
    existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
    conn.close()
    return existing is not None

# Raw substring matching let short/common keywords match inside unrelated
# words — "ios" inside "curiosity", "intern" inside "internal", "sales"
# inside "salesforce". Rarely surfaced with jobspy's short descriptions, but
# broke badly against full-length Greenhouse/Lever JD text (boilerplate
# "About Us"/benefits/EEO sections), e.g. "salesforce" mentioned in a tech
# stack list silently blocking an otherwise-qualifying posting. Same
# word-boundary fix as pipeline.role_matches() — underscore normalized to a
# space first since \b treats "_" as a word character.
_BOUNDARY_CACHE = {}

def _boundary_pattern(phrase):
    # Trailing "s?" catches plain plurals ("pipeline" -> "pipelines", "rest
    # api" -> "rest apis") for free. Confirmed live against the real
    # production database: "pipeline" is the candidate's headline resume
    # strength, yet 1,090 of 3,304 kept jobs (33%) mentioned it only in
    # plural form and got zero keyword credit for it under the old
    # exact-\b-match rule — silently under-scoring exactly the postings
    # that should have ranked highest. Irregular variants (postgres vs.
    # postgresql, next.js vs. nextjs, a/b testing vs. ab testing/a/b tests)
    # aren't simple pluralization and need their own explicit MATCH_KEYWORDS
    # entries instead — see job_filters.py.
    if phrase not in _BOUNDARY_CACHE:
        _BOUNDARY_CACHE[phrase] = re.compile(r"\b" + re.escape(phrase) + r"s?\b")
    return _BOUNDARY_CACHE[phrase]

def keyword_matches(text: str, keywords: list[str]):
    text = text.lower().replace("_", " ")
    return [kw for kw in keywords if _boundary_pattern(kw).search(text)]

def blocked_job(text: str, blocked_keywords: set[str]):
    text = text.lower().replace("_", " ")
    return any(_boundary_pattern(kw).search(text) for kw in blocked_keywords)
