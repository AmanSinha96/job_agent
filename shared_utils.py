import re
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
    return {"title": title, "company": company, "location": location, "description": description, "url": url, "salary": salary}

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
    if phrase not in _BOUNDARY_CACHE:
        _BOUNDARY_CACHE[phrase] = re.compile(r"\b" + re.escape(phrase) + r"\b")
    return _BOUNDARY_CACHE[phrase]

def keyword_matches(text: str, keywords: list[str]):
    text = text.lower().replace("_", " ")
    return [kw for kw in keywords if _boundary_pattern(kw).search(text)]

def blocked_job(text: str, blocked_keywords: set[str]):
    text = text.lower().replace("_", " ")
    return any(_boundary_pattern(kw).search(text) for kw in blocked_keywords)
