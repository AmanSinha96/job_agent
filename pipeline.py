import asyncio
import logging
import re
import time
import random
from datetime import datetime, timezone
import pandas as pd
from jobspy import scrape_jobs
from shared_utils import normalize_job, keyword_matches, blocked_job
from database import save_job, get_connection, load_dedup_state, normalize_url, job_fingerprint
from ats_detector import detect_ats
from job_filters import (
    TARGET_ROLES, ROLE_WORD_GROUPS, LOCATIONS, MATCH_KEYWORDS, MIN_MATCH_COUNT,
    BLOCKED_KEYWORDS, TITLE_ONLY_BLOCKED_KEYWORDS, BIG_COMPANY_MIN_EMPLOYEES,
    BIG_COMPANY_BONUS, MID_COMPANY_MIN_EMPLOYEES, MID_COMPANY_BONUS,
    MIN_EXPERIENCE_YEARS, EXCLUDED_ROLE_PHRASES, EXCLUDED_ROLE_REDEEMERS,
    KEYWORD_MATCH_CAP, KEYWORD_MATCH_WEIGHT, ATS_MATCH_BONUS, BLOCKED_COMPANIES,
)
from config import PROXIES, MIN_SALARY_LPA

# Raised from 30 -> 40 so a strong week doesn't get cut off; tailoring still
# only ever runs for the top 15 regardless (see cloud_run.TAILOR_TOP_N).
SELECT_TOP_N = 40

# Firehose sources (LinkedIn/Indeed/Naukri/job-board APIs) — reject anything
# whose own posted date is older than this, even if the site's "last N hours"
# filter returned it. Indeed's hours_old filters on "dateOnIndeed", which a
# repost/refresh bumps — confirmed live: a Metropolis Healthcare "Data
# Analyst" listing posted 2026-03-10 kept coming back as "new" through
# August. Day-granular dates (jobspy) mean "yesterday" can read up to ~48h,
# hence 72h rather than 24h. Watchlist (company career sites) has its own,
# deliberately longer ceiling — see career_sites.WATCHLIST_MAX_AGE_HOURS.
FIREHOSE_MAX_AGE_HOURS = 72

# select() only considers jobs saved within this window, so if a digest email
# fails for a few days, the backlog doesn't crowd out fresh postings forever.
SELECT_MAX_AGE_DAYS = 7

# Every source in the --sites list that isn't a jobspy site_name.
NON_JOBSPY_SOURCES = {"naukri", "watchlist", "remote_boards"}

# `board` values produced by career_sites.scrape_watchlist() — exempt from
# FIREHOSE_MAX_AGE_HOURS (they have their own 60-day ceiling).
WATCHLIST_BOARDS = {"greenhouse", "lever", "workday", "amazon", "smartrecruiters", "oracle_cloud", "ashby"}

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

    for phrase in EXCLUDED_ROLE_PHRASES:
        if re.search(r"\b" + re.escape(phrase) + r"\b", title):
            if not any(re.search(r"\b" + re.escape(w) + r"\b", title) for w in EXCLUDED_ROLE_REDEEMERS):
                return False

    return any(
        all(re.search(r"\b" + re.escape(word) + r"\b", title) for word in group)
        for group in ROLE_WORD_GROUPS
    )

_LOCATION_ALIASES = {"bengaluru": "bangalore", "gurugram": "gurgaon"}

def location_matches(location: str):
    location = location.lower()
    for alias, canonical in _LOCATION_ALIASES.items():
        location = location.replace(alias, canonical)
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
    # Keyword match is the dominant factor (up to KEYWORD_MATCH_WEIGHT
    # points), scaled against KEYWORD_MATCH_CAP so a genuinely strong match
    # count (not just clearing MIN_MATCH_COUNT) is what earns top marks.
    # ATS-type and company-size are real tiebreakers only now, not
    # co-equal factors — see job_filters.py for why (confirmed live against
    # production data: the old weights let big-company postings with fewer
    # real keyword matches outrank smaller/unlisted-size postings with a
    # visibly stronger match to the actual JD).
    text = (job["title"] + " " + job["description"]).lower()
    matches = keyword_matches(text, MATCH_KEYWORDS)
    ats_url = detect_ats(job["url"])

    keyword_score = min(len(matches) / KEYWORD_MATCH_CAP, 1.0) * KEYWORD_MATCH_WEIGHT
    ats_bonus = ATS_MATCH_BONUS if ats_url != "generic" else 0
    confidence = min(keyword_score + ats_bonus + company_size_bonus(job.get("company_size", 0)), 100)

    return confidence, matches, None

def should_keep(job: dict):
    text = (job["title"] + " " + job["description"]).lower()
    
    # 1. Role filter
    if not role_matches(job["title"]):
        return False, "role_mismatch"

    # 1b. Recruiting-marketplace company blocklist — see job_filters.py.
    if job["company"].strip().lower() in BLOCKED_COMPANIES:
        return False, "blocked_company"

    # 2. Location filter
    if not location_matches(job["location"]):
        return False, "location_mismatch"

    # 3. Blocked keywords — full JD text for most, title-only for terms
    # ("marketing"/"sales") that are only a meaningful "wrong job family"
    # signal when they describe the role itself, not when merely mentioned
    # in passing (candidate's actual specialty is marketing analytics).
    if blocked_job(text, BLOCKED_KEYWORDS): return False, "blocked_keyword"
    if blocked_job(job["title"].lower(), TITLE_ONLY_BLOCKED_KEYWORDS): return False, "blocked_keyword_title"

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
        "posted_at": job.get("posted_at"),
    }
    return save_job(record)

def _cell(value):
    """JobSpy DataFrame cells come back as NaN for missing data; normalize to None."""
    if isinstance(value, float) and pd.isna(value):
        return None
    return value

def _jobspy_age_hours(date_posted) -> float | None:
    # jobspy's date_posted is a plain date (day-granular, not a timestamp) —
    # same precision tradeoff already accepted for Workday's "Posted N Days
    # Ago" text elsewhere in this project. Previously fetched and immediately
    # discarded; now feeds the digest's posting-age column (see
    # cloud_run.build_digest_html).
    date_posted = _cell(date_posted)
    # _cell() only catches NaN (a float); jobspy's actual "missing" sentinel
    # for a date column is pd.NaT, a different type that isinstance(value,
    # float) doesn't match — confirmed live: an unguarded NaT silently
    # produced a ~2026-YEAR-old "age", not a missing value, because
    # pd.NaT.to_pydatetime() below doesn't raise, it returns datetime.min.
    if date_posted is None or pd.isna(date_posted):
        return None
    try:
        if hasattr(date_posted, "to_pydatetime"):
            date_posted = date_posted.to_pydatetime().date()
        dt = datetime.combine(date_posted, datetime.min.time(), tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return None

def _jobspy_location(row: dict, search_city: str) -> str:
    # Trust the city we searched for over each site's own location
    # formatting (Indeed often returns "KA, IN" instead of "Bangalore").
    if search_city != "Remote":
        return search_city
    # For the "Remote" search, only label it Remote if the posting actually
    # is — Indeed lets only one of hours_old / is_remote apply per query and
    # hours_old wins, so its "Remote" search really returns every India-wide
    # posting. Labelling all of those "Remote" let Mumbai/Delhi/Chennai
    # on-site roles straight through location_matches().
    site_location = str(_cell(row.get("location")) or "")
    if _cell(row.get("is_remote")) or "remote" in site_location.lower():
        return "Remote"
    return site_location

def _jobspy_row_to_raw(row: dict, search_city: str, site_override: str | None = None) -> dict:
    return {
        "title": _cell(row.get("title")),
        "company": _cell(row.get("company")),
        "location": _jobspy_location(row, search_city),
        "description": _cell(row.get("description")) or "",
        "url": _cell(row.get("job_url_direct")) or _cell(row.get("job_url")) or "",
        "salary": _salary_string(row),
        "board": site_override or _cell(row.get("site")) or "unknown",
        "company_size": parse_company_size(_cell(row.get("company_num_employees"))),
        "age_hours": _jobspy_age_hours(row.get("date_posted")),
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

    # Load dedup state once instead of a DB round-trip per scraped row.
    # Dedup is by normalized URL AND by company+title fingerprint — the same
    # req routinely appears under several URLs (one per city on LinkedIn, a
    # fresh tracking token per scrape on Indeed cross-posts, plus the same
    # req on several boards at once). See database.DUPLICATE_LOOKBACK_DAYS.
    existing_urls, existing_fingerprints = load_dedup_state()
    skipped = {"duplicate": 0, "too_old": 0, "filtered": 0}

    def process_and_save(raw: dict) -> bool:
        job = normalize_job(raw)
        job["url"] = normalize_url(job["url"])
        job["board"] = raw["board"]
        job["company_size"] = raw.get("company_size", 0)
        fingerprint = job_fingerprint(job["company"], job["title"])
        if not job["url"] or job["url"] in existing_urls or (fingerprint and fingerprint in existing_fingerprints):
            skipped["duplicate"] += 1
            return False
        age = raw.get("age_hours", raw.get("hours_ago"))
        if raw["board"] not in WATCHLIST_BOARDS and age is not None and age > FIREHOSE_MAX_AGE_HOURS:
            skipped["too_old"] += 1
            return False
        keep, result = should_keep(job)
        if not keep:
            skipped["filtered"] += 1
            return False
        if not save_processed_job(job, status="new"):
            skipped["duplicate"] += 1
            return False
        existing_urls.add(job["url"])
        if fingerprint:
            existing_fingerprints.add(fingerprint)
        return True

    saved = 0
    stale_watchlist_companies = []
    search_locations = list(locations) + ["Remote"]

    # Naukri goes through a real browser (Playwright) rather than jobspy —
    # jobspy's own Naukri scraper hits the API directly and gets a hard 406
    # every time. See naukri_playwright.py for why, and its expected ~25%
    # per-request failure rate (already handled there, not here).
    jobspy_sites = [s for s in sites if s.lower() not in NON_JOBSPY_SOURCES]
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

    if "watchlist" in [s.lower() for s in sites]:
        # Direct company-career-site sweep (Greenhouse/Lever/Workday APIs +
        # Amazon's own jobs API) — catches companies like Fractal that don't
        # cross-post to Indeed/LinkedIn/Naukri. See career_sites.py.
        from career_sites import scrape_watchlist
        try:
            watchlist_jobs, stale_watchlist_companies = await asyncio.to_thread(scrape_watchlist, roles, hours_old)
        except Exception as e:
            logger.error("Watchlist sweep failed entirely: %s", e)
            watchlist_jobs = []
        for raw in watchlist_jobs:
            if process_and_save(raw):
                saved += 1

    if "remote_boards" in [s.lower() for s in sites]:
        # Free public job-board APIs (remote roles open to India) — see
        # job_boards.py.
        from job_boards import scrape_remote_boards
        try:
            board_jobs = await asyncio.to_thread(scrape_remote_boards, roles, hours_old)
        except Exception as e:
            logger.error("Remote job-board sweep failed entirely: %s", e)
            board_jobs = []
        for raw in board_jobs:
            if process_and_save(raw):
                saved += 1

    if not jobspy_sites:
        logger.info("Sweep Phase complete. Saved %d new jobs (skipped: %s).", saved, skipped)
        return saved, stale_watchlist_companies

    # One scrape_jobs() call per site rather than all sites in one call —
    # jobspy re-raises any single site's exception for the whole call, so one
    # blocked site (e.g. Glassdoor 403ing a runner IP) would throw away every
    # other site's rows for that role/city too.
    per_site = {site: {"rows": 0, "saved": 0, "errors": 0} for site in jobspy_sites}
    for role in roles:
        for city in search_locations:
            is_remote = city == "Remote"
            for site in jobspy_sites:
                try:
                    df = scrape_jobs(
                        site_name=[site],
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
                    per_site[site]["errors"] += 1
                    logger.error("jobspy scrape failed for site=%s role=%s city=%s: %s", site, role, city, e)
                    continue

                if df is None or df.empty:
                    continue

                per_site[site]["rows"] += len(df)
                for _, row in df.iterrows():
                    raw = _jobspy_row_to_raw(row.to_dict(), city)
                    if process_and_save(raw):
                        saved += 1
                        per_site[site]["saved"] += 1

            time.sleep(random.uniform(10, 30))

    logger.info("jobspy per-site results: %s", per_site)

    logger.info("Sweep Phase complete. Saved %d new jobs (skipped: %s).", saved, skipped)
    return saved, stale_watchlist_companies

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

    # 2. Select top N by confidence (JD/keyword match + company-size boost),
    # among recently saved jobs only — see SELECT_MAX_AGE_DAYS.
    top_picks = conn.execute("""
        SELECT id FROM jobs
        WHERE status='ranked' AND scraped_at >= datetime('now', ?)
        ORDER BY confidence DESC, scraped_at DESC
        LIMIT ?
    """, (f"-{SELECT_MAX_AGE_DAYS} days", SELECT_TOP_N)).fetchall()

    # 3. Mark as top_pick
    for row in top_picks:
        conn.execute("UPDATE jobs SET status='top_pick' WHERE id=?", (row['id'],))

    conn.commit()
    conn.close()
    logger.info("Select Phase complete. Selected %d jobs for top_pick.", len(top_picks))

async def run(sites: list[str], hours_old: int, roles=TARGET_ROLES, locations=LOCATIONS):
    """Main workflow orchestration."""
    saved, stale_watchlist_companies = await sweep(sites, hours_old, roles, locations)
    rank()
    select()
    logger.info("Pipeline complete.")
    return saved, stale_watchlist_companies
