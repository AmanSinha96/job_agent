import asyncio
import logging
import re
import time
import random
import pandas as pd
from jobspy import scrape_jobs
from shared_utils import normalize_job, keyword_matches, blocked_job
from database import save_job, get_connection
from ats_detector import detect_ats
from resume_scorer import score_resume
from job_filters import (
    TARGET_ROLES, ROLE_WORD_GROUPS, LOCATIONS, MATCH_KEYWORDS, MIN_MATCH_COUNT,
    BLOCKED_KEYWORDS, BIG_COMPANY_MIN_EMPLOYEES, BIG_COMPANY_BONUS,
    MID_COMPANY_MIN_EMPLOYEES, MID_COMPANY_BONUS, MIN_EXPERIENCE_YEARS,
)
from config import PROXIES, MIN_SALARY_LPA

# Raised from 30 -> 40 so a strong week doesn't get cut off; tailoring still
# only ever runs for the top 15 regardless (see cloud_run.TAILOR_TOP_N).
SELECT_TOP_N = 40

logger = logging.getLogger(__name__)

# --- Helper logic moved from scraper.py ---

def url_tier(url: str) -> int:
    # URL tiers logic
    tier_1 = ("myworkdayjobs.com", "greenhouse.io", "lever.co", "smartrecruiters.com", "ashbyhq.com", "icims.com", "jobvite.com", "taleo.net")
    if any(t in url for t in tier_1): return 1
    if any(t in url for t in ("careers.", "/careers/", "/jobs/")): return 2
    if any(t in url for t in ("indeed.com", "glassdoor.com")): return 3
    if "linkedin.com" in url: return 4
    return 5

def make_job_id(title: str, company: str, url: str) -> str:
    return str(hash(f"{title}{company}{url}"))

def extract_salary_lpa(text: str):
    patterns = [r"(\d+)\s*lpa", r"(\d+)\s*k"]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    return None

def extract_experience_range(text: str) -> tuple[int, int] | None:
    """Best-effort extraction of a stated experience requirement as
    (lower, upper) bounds — "5-8 years" -> (5, 8), "6+ years" -> (6, 6),
    "minimum 5 years" -> (5, 5). Range pattern must be checked first: a
    bare '\\d+ years?' regex applied to "5-8 years" matches "8" (the number
    immediately before "years"), not the intended lower bound "5"."""
    text = text.lower()

    m = re.search(r"(\d+)\s*[-–to]+\s*(\d+)\+?\s*(?:years?|yrs?)", text)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = re.search(r"(\d+)\+\s*(?:years?|yrs?)", text)
    if m:
        return int(m.group(1)), int(m.group(1))

    m = re.search(r"(?:minimum|min\.?|at least)\s*(\d+)\s*(?:years?|yrs?)", text)
    if m:
        return int(m.group(1)), int(m.group(1))

    m = re.search(r"(\d+)\s*(?:years?|yrs?)", text)
    if m:
        return int(m.group(1)), int(m.group(1))

    return None

def salary_to_lpa(salary_text: str | None) -> float | None:
    """Parse a salary string into an approximate UPPER-bound LPA figure —
    a "15-25 LPA" range means a strong candidate can plausibly land at 25,
    so the ceiling (not the floor) is what should be compared against a
    minimum-salary requirement; using the floor rejected ranges that
    clearly reach the target further up.
    Naukri text is already labeled in Lacs ("13-23 Lacs PA"); jobspy-sourced
    strings are raw annual rupee amounts (already annualized in
    _salary_string() below) with no such label — divide by 100,000."""
    if not salary_text:
        return None
    text = salary_text.lower()
    numbers = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+\.?\d*", text)]
    if not numbers:
        return None
    if "lac" in text or "lakh" in text or "lpa" in text:
        return max(numbers)
    if "inr" not in text and any(c.isalpha() for c in text.replace("inr", "")):
        return None  # non-INR currency — not comparable without FX conversion
    return max(numbers) / 100000

# --- Pipeline logic ---

def role_matches(title: str):
    # Word-boundary match, not raw substring — short tokens like "ai"/"bi"
    # otherwise match inside unrelated words ("airflow" contains "ai",
    # "cabin" would contain "bi"). Confirmed live: "Senior Data Engineer
    # (AWS EMR, Spark & Airflow)" was false-matching the {"ai","engineer"}
    # group purely because "airflow" contains "ai" as a substring.
    # Underscore is treated as a word character by regex \b, but real
    # postings use it as an informal separator ("Data Analyst_3+yrs",
    # "AI Engineer_ MLOps") — normalize it to a space first or those
    # wouldn't get a boundary where one's clearly intended.
    title = title.lower().replace("_", " ")
    return any(
        all(re.search(r"\b" + re.escape(word) + r"\b", title) for word in group)
        for group in ROLE_WORD_GROUPS
    )

def location_matches(location: str):
    location = location.lower()
    return any(loc.lower() in location for loc in LOCATIONS) or "remote" in location

def parse_company_size(size_text) -> int:
    """jobspy reports company size as a bucket string ('10,000+', '1,001 to 5,000').
    Extract the largest number in it as an approximate headcount."""
    if not size_text or not isinstance(size_text, str):
        return 0
    numbers = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", size_text)]
    return max(numbers) if numbers else 0

def company_size_bonus(employees: int) -> int:
    if employees >= BIG_COMPANY_MIN_EMPLOYEES:
        return BIG_COMPANY_BONUS
    if employees >= MID_COMPANY_MIN_EMPLOYEES:
        return MID_COMPANY_BONUS
    return 0

def compute_confidence(job: dict):
    # Calculate components
    text = (job["title"] + " " + job["description"]).lower()
    matches = keyword_matches(text, MATCH_KEYWORDS)
    ats_url = detect_ats(job["url"])

    # Fake/Placeholder ats_result structure
    ats_result = {"score": 100 if ats_url != "generic" else 0}

    # Use imported score_resume, then boost jobs from bigger companies —
    # tiebreaker on top of the actual JD/keyword match, not a replacement for it.
    confidence = score_resume(len(matches), ats_result)
    confidence = min(confidence + company_size_bonus(job.get("company_size", 0)), 100)

    return confidence, matches, None

def should_keep(job: dict):
    text = (job["title"] + " " + job["description"]).lower()
    
    # 1. Role filter
    if not role_matches(job["title"]):
        return False, "role_mismatch"
        
    # 2. Location filter
    if not location_matches(job["location"]):
        return False, "location_mismatch"

    # 3. Blocked keywords
    if blocked_job(text, BLOCKED_KEYWORDS): return False, "blocked_keyword"
    
    # 4. Keyword match requirement
    matches = keyword_matches(text, MATCH_KEYWORDS)
    if len(matches) < MIN_MATCH_COUNT:
        return False, f"insufficient_keywords: {len(matches)}"

    # 5. Basic checks
    if len(job["title"]) < 3: return False, "invalid_title"
    if not job["company"]: return False, "missing_company"
    if not job["url"]: return False, "missing_url"

    # 6. Experience floor — candidate is 6+ years, don't surface junior/
    # mid-junior postings. Checks the UPPER bound of a stated range: a
    # "3-8 years" posting clearly still fits a 6-year candidate even though
    # its floor is below 5 — only reject when even the ceiling doesn't
    # reach it (e.g. "0-3 years"). JDs that don't mention years at all
    # aren't penalized for silence.
    exp_range = extract_experience_range(text)
    if exp_range is not None and exp_range[1] < MIN_EXPERIENCE_YEARS:
        return False, f"experience_too_low: {exp_range[0]}-{exp_range[1]}y stated, need {MIN_EXPERIENCE_YEARS}y+"

    # 7. Salary floor — same upper-bound logic: a "15-25 LPA" posting can
    # plausibly land at 25 for a strong candidate, so only reject when the
    # ceiling itself is below the floor. Unlisted salary never blocks a job.
    salary_lpa = salary_to_lpa(job.get("salary"))
    if salary_lpa is not None and salary_lpa < MIN_SALARY_LPA:
        return False, f"salary_below_floor: ceiling {salary_lpa} LPA, need {MIN_SALARY_LPA}+"

    # Re-calculate confidence
    confidence = min(len(matches) * 15, 100)

    if confidence < 25: return False, "low_confidence"
    return True, {"confidence": confidence, "matches": matches, "salary": None}

def save_processed_job(job, status="new"):
    confidence, matches, salary = compute_confidence(job)
    ats = detect_ats(job["url"])
    record = {
        "job_id": make_job_id(job["title"], job["company"], job["url"]),
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "board": job.get("board", "unknown"),
        "url": job["url"],
        "salary": job["salary"] or extract_salary_lpa(job["description"]),
        "confidence": confidence / 100,
        "status": status,
        "failure_reason": None,
        "ats": ats,
        "tailored_resume": None,
        "screenshot": None,
        "notes": "",
        "pdf_path": None,
        "url_tier": url_tier(job["url"]),
        "resume_score": confidence,
        "keyword_match": len(matches),
        "keywords": ",".join(matches),
        "description": job["description"],
        "company_size": job.get("company_size", 0),
    }
    save_job(record)
    return record

def _cell(value):
    """JobSpy DataFrame cells come back as NaN for missing data; normalize to None."""
    if isinstance(value, float) and pd.isna(value):
        return None
    return value

def _jobspy_row_to_raw(row: dict, search_city: str, site_override: str | None = None) -> dict:
    return {
        "title": _cell(row.get("title")),
        "company": _cell(row.get("company")),
        # Trust the city/"Remote" we searched for over each site's own location
        # formatting (Indeed often returns "KA, IN" instead of "Bangalore").
        "location": search_city,
        "description": _cell(row.get("description")) or "",
        "url": _cell(row.get("job_url_direct")) or _cell(row.get("job_url")) or "",
        "salary": _salary_string(row),
        "board": site_override or _cell(row.get("site")) or "unknown",
        "company_size": parse_company_size(_cell(row.get("company_num_employees"))),
    }

_INTERVAL_TO_ANNUAL_MULTIPLIER = {"monthly": 12, "weekly": 52, "daily": 260, "hourly": 2080}

def _salary_string(row: dict) -> str | None:
    lo, hi, currency, interval = (
        _cell(row.get("min_amount")), _cell(row.get("max_amount")),
        _cell(row.get("currency")), _cell(row.get("interval")),
    )
    if not lo and not hi:
        return None
    # Annualize here so every downstream consumer (salary_to_lpa()) can
    # assume "raw number = annual" without re-deriving interval — a monthly
    # salary was previously being compared as if it were already annual.
    multiplier = _INTERVAL_TO_ANNUAL_MULTIPLIER.get((interval or "").lower(), 1)
    parts = [str(int(v * multiplier)) for v in (lo, hi) if v]
    return f"{currency or ''} {'-'.join(parts)}".strip()

async def sweep(sites: list[str], hours_old: int, roles=TARGET_ROLES, locations=LOCATIONS):
    """Phase 1: Sweep new jobs via python-jobspy across the given sites."""
    logger.info("Running Sweep Phase for sites=%s, hours_old=%s...", sites, hours_old)

    # Load existing URLs once instead of a DB round-trip per scraped row
    # (already_exists() opens+closes a connection every call — across
    # hundreds of rows per sweep that adds up for no reason).
    conn = get_connection()
    existing_urls = {row["url"] for row in conn.execute("SELECT url FROM jobs").fetchall()}
    conn.close()

    def process_and_save(raw: dict) -> bool:
        job = normalize_job(raw)
        job["board"] = raw["board"]
        job["company_size"] = raw.get("company_size", 0)
        if not job["url"] or job["url"] in existing_urls:
            return False
        keep, result = should_keep(job)
        if not keep:
            return False
        save_processed_job(job, status="new")
        existing_urls.add(job["url"])
        return True

    saved = 0
    search_locations = list(locations) + ["Remote"]

    # Naukri goes through a real browser (Playwright) rather than jobspy —
    # jobspy's own Naukri scraper hits the API directly and gets a hard 406
    # every time. See naukri_playwright.py for why, and its expected ~25%
    # per-request failure rate (already handled there, not here).
    jobspy_sites = [s for s in sites if s.lower() != "naukri"]
    if "naukri" in [s.lower() for s in sites]:
        from naukri_playwright import scrape_naukri
        try:
            # Playwright's sync API refuses to run inside an already-running
            # asyncio loop (which sweep() is) — run it in a worker thread.
            naukri_jobs = await asyncio.to_thread(scrape_naukri, roles, locations, hours_old)
        except Exception as e:
            logger.error("Naukri (Playwright) sweep failed entirely: %s", e)
            naukri_jobs = []
        for raw in naukri_jobs:
            if process_and_save(raw):
                saved += 1

    if not jobspy_sites:
        logger.info("Sweep Phase complete. Saved %d new jobs.", saved)
        return saved

    for role in roles:
        for city in search_locations:
            is_remote = city == "Remote"
            try:
                df = scrape_jobs(
                    site_name=jobspy_sites,
                    search_term=role,
                    google_search_term=f"{role} jobs in {city}, India",
                    location="India" if is_remote else f"{city}, India",
                    is_remote=is_remote,
                    results_wanted=40,
                    hours_old=hours_old,
                    country_indeed="India",
                    linkedin_fetch_description=True,
                    proxies=PROXIES or None,
                )
            except Exception as e:
                logger.error("jobspy scrape failed for role=%s city=%s: %s", role, city, e)
                continue

            if df is None or df.empty:
                time.sleep(random.uniform(10, 30))
                continue

            for _, row in df.iterrows():
                raw = _jobspy_row_to_raw(row.to_dict(), city)
                if process_and_save(raw):
                    saved += 1

            time.sleep(random.uniform(10, 30))

    logger.info("Sweep Phase complete. Saved %d new jobs.", saved)
    return saved

def rank():
    """Phase 2: Rank 'new' jobs."""
    logger.info("Running Rank Phase...")
    conn = get_connection()
    new_jobs = conn.execute("SELECT * FROM jobs WHERE status='new'").fetchall()
    
    for job in new_jobs:
        # Calculate confidence — divide by 100 to match save_processed_job()'s
        # 0-1 scale for this column (compute_confidence() itself returns 0-100).
        confidence, _, _ = compute_confidence(dict(job))

        # Update confidence and status to 'ranked'
        conn.execute("UPDATE jobs SET confidence=?, status='ranked' WHERE id=?", (confidence / 100, job['id']))
    
    conn.commit()
    conn.close()
    logger.info("Rank Phase complete. Ranked %d jobs.", len(new_jobs))

def select():
    """Phase 3: Select top N (best match + biggest companies)."""
    logger.info("Running Select Phase...")
    conn = get_connection()

    # 1. Reset current top_pick to ranked
    conn.execute("UPDATE jobs SET status='ranked' WHERE status='top_pick'")

    # 2. Select top N by confidence (JD/keyword match + company-size boost)
    top_picks = conn.execute("""
        SELECT id FROM jobs
        WHERE status='ranked'
        ORDER BY confidence DESC, scraped_at DESC
        LIMIT ?
    """, (SELECT_TOP_N,)).fetchall()

    # 3. Mark as top_pick
    for row in top_picks:
        conn.execute("UPDATE jobs SET status='top_pick' WHERE id=?", (row['id'],))

    conn.commit()
    conn.close()
    logger.info("Select Phase complete. Selected %d jobs for top_pick.", len(top_picks))

async def run(sites: list[str], hours_old: int, roles=TARGET_ROLES, locations=LOCATIONS):
    """Main workflow orchestration."""
    saved = await sweep(sites, hours_old, roles, locations)
    rank()
    select()
    logger.info("Pipeline complete.")
    return saved
