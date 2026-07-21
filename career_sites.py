"""
career_sites.py — direct company/ATS sweep, the 4th job source alongside
Indeed/LinkedIn (jobspy) and Naukri (naukri_playwright.py).

Why this exists: Indeed/LinkedIn/Naukri only surface whatever those
aggregators happen to index. Some companies (Fractal, boutique product
shops, etc.) don't cross-post broadly and are effectively invisible to the
other three sources no matter how the search terms are tuned. This module
hits each company's own careers-site API directly instead.

Three ATS platforms expose a free, public, unauthenticated JSON API for
their own job board — no scraping, no anti-bot risk:
  - Greenhouse: boards-api.greenhouse.io
  - Lever:      api.lever.co
  - Workday:    {tenant}.{host}.myworkdayjobs.com/wday/cxs/...
Amazon isn't on any of the three but runs its own public jobs API
(amazon.jobs), so it gets a dedicated fetch function.

This only covers companies on one of these platforms. Large enterprises
often run Workday behind a custom domain (harder to find the tenant) or a
fully proprietary system (DE Shaw, confirmed 2026-07 — no public API,
deliberately not scraped here to avoid reintroducing the fragile per-site
Playwright scrapers this project already moved away from). Adding a new
company is: confirm its slug/tenant actually returns HTTP 200 with real
jobs (see conversation for the curl checks used), then add one line below.

Finding a Workday tenant: open the company's public "careers" URL and
watch where it redirects — Workday-hosted boards redirect to
"{tenant}.{host}.myworkdayjobs.com/{site}" (host is a shard, "wd1"/"wd3"/
"wd5"/etc — try a few if the obvious one 404s).
"""
import re
import time
import logging
import requests
import concurrent.futures
from datetime import datetime, timezone
from curl_cffi import requests as curl_requests

from job_filters import LOCATIONS

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 20

# curl_cffi's `timeout=` param doesn't reliably bound wall-clock time —
# confirmed live: a call configured with timeout=20 hung for 195s instead.
# Standard HTTP client "timeout" semantics are usually a read/idle timeout
# (time between bytes), not a total-request deadline, so a connection that
# trickles data slowly (or a CDN that holds it open) can run far longer
# than the nominal value. A thread-pool future timeout enforces a true
# wall-clock cap regardless of what's happening inside curl_cffi/libcurl —
# if the call hasn't returned in time we just give up and move on, even
# though the orphaned network call may keep running in the background
# until it eventually times out on its own.
_WORKDAY_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)
_HARD_TIMEOUT = 25

# Total time budget for the whole Workday phase across all configured
# companies in one sweep — see scrape_watchlist() for why this exists.
# 600s leaves plenty of the frequent workflow's 40-minute job budget for
# jobspy scraping + resume tailoring + email, which run in the same job.
WORKDAY_BUDGET_SECONDS = 600


def _call_with_hard_timeout(fn, timeout=_HARD_TIMEOUT):
    future = _WORKDAY_EXECUTOR.submit(fn)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"hard wall-clock timeout after {timeout}s")

# Confirmed live 2026-07 — each slug actually returns HTTP 200 with real
# postings (see conversation for the verification curls). Add more by
# checking https://boards-api.greenhouse.io/v1/boards/{slug}/jobs first.
GREENHOUSE_COMPANIES = {
    "Groww": "groww",
    "Postman": "postman",
    "Stripe": "stripe",
    "Databricks": "databricks",
    "Twilio": "twilio",
    "MongoDB": "mongodb",
    "Elastic": "elastic",
}

# Confirmed live 2026-07 — check https://api.lever.co/v0/postings/{slug}?mode=json first.
LEVER_COMPANIES = {
    "CRED": "cred",
    "Meesho": "meesho",
    "Freshworks": "freshworks",
    "Zeta": "zeta",
}

# Confirmed live 2026-07 — each tenant/host/site returns HTTP 200 with real
# India-relevant postings (see conversation for the verification method:
# WebSearch to find the myworkdayjobs.com link on the company's real careers
# page, then curl_cffi(impersonate="chrome") to confirm the API + check
# searchText="India"/city names actually returns something).
#
# Checked but deliberately NOT added — confirmed not on Workday: Intuit
# (TalentBrew/Radancy), Lattice Semiconductor (iCIMS), AMD (iCIMS), Charles
# Schwab (TalentBrew/iCIMS), eBay (Phenom People — the only "ebay" Workday
# tenant found belongs to TCGplayer, an unrelated eBay-owned subsidiary),
# Dell (migrated to Oracle Fusion Cloud Recruiting), IBM (own platform),
# Infosys/Hexaware (own portals — expected for Indian IT services majors),
# Cognizant (the only "cognizant"-adjacent tenant is Collaborative
# Solutions, a Workday-implementation consulting arm it acquired — zero
# data/analytics roles, all internal Workday-consultant hiring).
#
# Checked, on Workday, but NOT added — confirmed real, near-zero India
# relevance for these target roles: PNC Bank (0 real India postings, only
# false "IN"/Indiana matches), Netflix (7 India postings, all
# non-technical Mumbai media/legal roles), Home Depot (0 India presence at
# all — India results are Indianapolis).
#
# Round 2 (2026-07) — checked but NOT added:
# - Not on Workday/Greenhouse/Lever at all (own ATS): Oracle (own Recruiting
#   Cloud), SAP/Capgemini (SuccessFactors), ServiceNow (SmartRecruiters),
#   Snowflake/PepsiCo (Phenom People), Atlassian (Lever account exists but
#   empty/inactive), Palo Alto Networks (SmartRecruiters), Confluent/UiPath
#   (Ashby), Deloitte (Avature), EY (SuccessFactors), KPMG/JPMorgan Chase/
#   American Express (Oracle Cloud HCM), TCS (own NextStep/iBegin portals),
#   Wipro (SuccessFactors), Tech Mahindra (custom ASP.NET portal), Goldman
#   Sachs (own higher.gs.com + Oracle Cloud), UnitedHealth Group (Oracle
#   Taleo).
# - On Workday/Greenhouse/Lever but confirmed ~zero India relevance for
#   these target roles: Palantir (Lever, 0 India postings at all), C3.ai
#   (Greenhouse "c3iot", 0 India postings), VMware/Broadcom (Workday, India
#   presence is real but zero Data/Analytics/BI hits — all Software/QA
#   Engineer).
WORKDAY_COMPANIES = {
    "Fractal": {"tenant": "fractal", "host": "wd1", "site": "Careers"},
    "Commonwealth Bank": {"tenant": "cba", "host": "wd3", "site": "CommBank_Careers"},
    "NVIDIA": {"tenant": "nvidia", "host": "wd5", "site": "NVIDIAExternalCareerSite"},
    "Salesforce": {"tenant": "salesforce", "host": "wd12", "site": "External_Career_Site"},
    "Adobe": {"tenant": "adobe", "host": "wd5", "site": "external_experienced"},
    "Applied Materials": {"tenant": "amat", "host": "wd1", "site": "External"},
    "Bank of America": {"tenant": "ghr", "host": "wd1", "site": "Lateral-ba_continuum"},
    "Morgan Stanley": {"tenant": "ms", "host": "wd5", "site": "External"},
    "BlackRock": {"tenant": "blackrock", "host": "wd1", "site": "BlackRock_Professional"},
    "Visa": {"tenant": "visa", "host": "wd5", "site": "Visa"},
    "Mastercard": {"tenant": "mastercard", "host": "wd1", "site": "CorporateCareers"},
    "FIS": {"tenant": "fis", "host": "wd5", "site": "SearchJobs"},
    "Walmart": {"tenant": "walmart", "host": "wd504", "site": "WalmartExternal"},
    "Target": {"tenant": "target", "host": "wd5", "site": "targetcareers"},
    "HPE": {"tenant": "hpe", "host": "wd5", "site": "ACJobSite"},
    "HP Inc": {"tenant": "hp", "host": "wd5", "site": "ExternalCareerSite"},
    "Equinix": {"tenant": "equinix", "host": "wd1", "site": "External"},
    "GE Aerospace": {"tenant": "geaerospace", "host": "wd5", "site": "GE_ExternalSite"},
    "GE Vernova": {"tenant": "gevernova", "host": "wd5", "site": "Vernova_ExternalSite"},
    "GE HealthCare": {"tenant": "gehc", "host": "wd5", "site": "GEHC_ExternalSite"},
    "DXC Technology": {"tenant": "dxctechnology", "host": "wd1", "site": "DXCJobs"},
    "PwC": {"tenant": "pwc", "host": "wd3", "site": "Global_Experienced_Careers"},
    "Accenture": {"tenant": "accenture", "host": "wd103", "site": "AccentureCareers"},
    "Genpact": {"tenant": "genpact", "host": "wd108", "site": "External_Careers"},
    "Wells Fargo": {"tenant": "wf", "host": "wd1", "site": "WellsFargoJobs"},
    "Capital One": {"tenant": "capitalone", "host": "wd12", "site": "Capital_One"},
    "Procter & Gamble": {"tenant": "pg", "host": "wd5", "site": "1000"},
    "Johnson & Johnson": {"tenant": "jj", "host": "wd5", "site": "JJ"},
    "Cisco": {"tenant": "cisco", "host": "wd5", "site": "Cisco_Careers"},
    "Pfizer": {"tenant": "pfizer", "host": "wd1", "site": "PfizerCareers"},
    "Verizon": {"tenant": "verizon", "host": "wd12", "site": "verizon-careers"},
    "AT&T": {"tenant": "att", "host": "wd1", "site": "ATTGeneral"},
}

# Approximate headcount for company_size_bonus() (see job_filters.py) —
# jobspy-sourced jobs get this from Indeed/LinkedIn's own data; these
# watchlist sources don't return it, so it's hardcoded per known company.
COMPANY_SIZE_HINTS = {
    "Fractal": 5000, "Groww": 1500, "Postman": 600, "Stripe": 8000,
    "Databricks": 7000, "Twilio": 5000, "CRED": 2000, "Meesho": 5000,
    "Freshworks": 5000, "Zeta": 3000, "Amazon": 1500000, "Commonwealth Bank": 50000,
    "NVIDIA": 30000, "Salesforce": 70000, "Adobe": 30000, "Applied Materials": 34000,
    "Bank of America": 215000, "Morgan Stanley": 80000, "BlackRock": 20000,
    "Visa": 26000, "Mastercard": 33000, "FIS": 55000, "Walmart": 2100000,
    "Target": 440000, "HPE": 62000, "HP Inc": 50000, "Equinix": 13000,
    "GE Aerospace": 52000, "GE Vernova": 80000, "GE HealthCare": 51000,
    "DXC Technology": 130000, "PwC": 370000,
    "MongoDB": 5000, "Elastic": 3000, "Accenture": 750000, "Genpact": 125000,
    "Wells Fargo": 230000, "Capital One": 55000, "Procter & Gamble": 108000,
    "Johnson & Johnson": 140000, "Cisco": 85000, "Pfizer": 83000,
    "Verizon": 105000, "AT&T": 140000,
}


def _strip_html(html):
    return re.sub(r"<[^>]+>", " ", html or "")


def _normalize_location(text):
    # Greenhouse/Lever/Workday all return "Bengaluru", which doesn't match
    # LOCATIONS' "Bangalore" — same fix naukri_playwright.py needed.
    return re.sub(r"bengaluru", "Bangalore", text or "", flags=re.IGNORECASE)


def _is_india_relevant(location):
    # pipeline.location_matches() treats ANY "remote" mention as a match —
    # safe for jobspy/naukri, which searched per-city and overrode the
    # location field with what was searched for (see pipeline.py). This
    # source has no such scoping: it pulls a company's entire global
    # postings list, so "Remote (US/Canada)" or "Remote - EMEA" would
    # otherwise slip through as a false "Remote" match. Confirmed live: a
    # Stripe "Remote (US/Canada)"-only posting passed should_keep() before
    # this filter was added. Require an actual India signal instead.
    loc = (location or "").lower()
    return "india" in loc or any(city.lower() in loc for city in LOCATIONS)


def _hours_since(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _greenhouse_age_hours(job):
    # first_published is the original posting date; updated_at can be recent
    # even for an old posting if the employer just edited the JD text, which
    # would wrongly make a stale posting look fresh.
    ts = job.get("first_published") or job.get("updated_at")
    if not ts:
        return None
    try:
        return _hours_since(datetime.fromisoformat(ts))
    except Exception:
        return None


def _lever_age_hours(job):
    ts = job.get("createdAt")
    if not ts:
        return None
    try:
        return _hours_since(datetime.fromtimestamp(ts / 1000, tz=timezone.utc))
    except Exception:
        return None


def _workday_age_hours(posted_on):
    # Confirmed live formats: "Posted Today", "Posted Yesterday",
    # "Posted N Days Ago", "Posted 30+ Days Ago" (open-ended — treated as
    # exactly 30 days, which is already far past any real hours_old ceiling
    # used in this project, so the imprecision doesn't matter).
    if not posted_on:
        return None
    text = posted_on.lower().strip()
    if "today" in text:
        return 0.0
    if "yesterday" in text:
        return 24.0
    m = re.search(r"(\d+)\+?\s*day", text)
    if m:
        return int(m.group(1)) * 24.0
    return None


def _amazon_age_hours(posted_date):
    if not posted_date:
        return None
    try:
        dt = datetime.strptime(posted_date, "%B %d, %Y").replace(tzinfo=timezone.utc)
        return _hours_since(dt)
    except Exception:
        return None


def _raw(title, company, location, description, url, board, age_hours=None):
    return {
        "title": title or "",
        "company": company,
        "location": _normalize_location(location),
        "description": description or "",
        "url": url or "",
        "salary": None,
        "board": board,
        "company_size": COMPANY_SIZE_HINTS.get(company, 0),
        "age_hours": age_hours,
    }


def fetch_greenhouse(company, slug):
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            params={"content": "true"}, headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Greenhouse fetch failed for %s: %s", company, e)
        return []
    return [
        _raw(j.get("title"), company, (j.get("location") or {}).get("name", ""),
             _strip_html(j.get("content")), j.get("absolute_url"), "greenhouse",
             age_hours=_greenhouse_age_hours(j))
        for j in resp.json().get("jobs", [])
    ]


def fetch_lever(company, slug):
    try:
        resp = requests.get(
            f"https://api.lever.co/v0/postings/{slug}",
            params={"mode": "json"}, headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Lever fetch failed for %s: %s", company, e)
        return []
    jobs = []
    for j in resp.json():
        cat = j.get("categories") or {}
        jobs.append(_raw(
            j.get("text"), company, cat.get("location", ""),
            j.get("descriptionPlain") or _strip_html(j.get("description")),
            j.get("hostedUrl"), "lever",
            age_hours=_lever_age_hours(j),
        ))
    return jobs


def fetch_workday(company, tenant, host, site, roles, role_matches, hours_old=None):
    # Workday's Cloudflare front blocks plain `requests` by TLS fingerprint
    # (JA3) alone — confirmed live: identical payload/headers, curl and
    # curl_cffi(impersonate="chrome") get HTTP 200, `requests` gets HTTP 400,
    # consistently. Only Workday needs this; Greenhouse/Lever/Amazon's APIs
    # don't fingerprint-block plain `requests`.
    #
    # Even with curl_cffi, Cloudflare's bot-scoring is probabilistic, not a
    # deterministic block — confirmed live: back-to-back identical requests
    # from the same process got 200, then 400, then 200 again with no code
    # change. Same inherent flakiness already documented for Naukri (see
    # naukri_playwright.py, ~75% per-request success rate) — a couple of
    # retries recovers most of it; treat remaining failures as expected,
    # not a bug to keep chasing.
    base = f"https://{tenant}.{host}.myworkdayjobs.com"

    def search(text):
        for attempt in range(2):
            try:
                # Page size of 20 is confirmed safe across tenants — some
                # (Commonwealth Bank's "cba" tenant) hard-reject limit=50
                # with a 400 even though Fractal's tenant accepts it fine.
                resp = _call_with_hard_timeout(lambda: curl_requests.post(
                    f"{base}/wday/cxs/{tenant}/{site}/jobs",
                    json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": text},
                    impersonate="chrome", timeout=_TIMEOUT,
                ))
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.warning("Workday search attempt %d failed for %s (%r): %s",
                                attempt + 1, company, text, e)
        return None

    # Search per role term via Workday's own full-text search instead of
    # paginating a company's entire catalog and filtering client-side —
    # some watchlist companies have 2,000-4,500+ total postings (Walmart,
    # Target, PwC), and blindly paginating all of them per run isn't worth
    # it just to find a handful of relevant titles. searchText also matches
    # against description text, not just title, so it's more thorough than
    # a title-only regex would be — role_matches() below still gates on
    # title afterward since a fuzzy full-text search returns some noise.
    postings, seen_paths = [], set()
    for role in roles:
        page = search(role)
        if page is None:
            continue
        for p in page.get("jobPostings", []):
            path = p.get("externalPath")
            if path and path not in seen_paths:
                seen_paths.add(path)
                postings.append(p)

    jobs = []
    for p in postings:
        title = p.get("title", "")
        # Skip the per-job detail fetch (a separate HTTP call) unless the
        # title itself passes the same role check used everywhere else —
        # searchText matches against description text too, so a role name
        # can surface a title that isn't actually that role.
        if not role_matches(title):
            continue
        # Skip the detail fetch (a separate, slower HTTP call) entirely for
        # postings already too old — postedOn is available for free in the
        # list response, no need to pay for a detail fetch just to filter
        # it out afterward anyway. Confirmed live: Accenture alone returned
        # 57 role-matched titles, most 5-30+ days old, taking 100s total —
        # this cuts that down to only the ones that could actually survive
        # scrape_watchlist()'s age filter.
        early_age = _workday_age_hours(p.get("postedOn"))
        if hours_old is not None and early_age is not None and early_age > hours_old:
            continue
        path = p.get("externalPath", "")
        description = ""
        try:
            # API detail path (returns JSON) differs from the public browsable
            # URL below — confirmed live: /wday/cxs/{tenant}/{site}{path}.
            detail = _call_with_hard_timeout(lambda: curl_requests.get(
                f"{base}/wday/cxs/{tenant}/{site}{path}", impersonate="chrome", timeout=_TIMEOUT))
            detail.raise_for_status()
            description = _strip_html(detail.json().get("jobPostingInfo", {}).get("jobDescription", ""))
        except Exception as e:
            logger.warning("Workday detail fetch failed for %s %s: %s", company, title, e)
        # Some tenants (confirmed live: Accenture's "accenture" tenant) don't
        # return locationsText at all — location is instead the second
        # element of bulletFields (the first is always the requisition ID),
        # e.g. ["R00327660", "Ebene"]. Without this fallback every posting
        # from those tenants silently loses its location and gets dropped
        # by _is_india_relevant() regardless of where it actually is.
        location = p.get("locationsText") or ""
        if not location:
            bullets = p.get("bulletFields") or []
            if len(bullets) > 1:
                location = bullets[1]
        jobs.append(_raw(title, company, location, description,
                          f"{base}/{site}{path}", "workday",
                          age_hours=_workday_age_hours(p.get("postedOn"))))
    # Returns raw `postings` count alongside the filtered `jobs` list —
    # `len(jobs)` reflects role-match AND recency, both of which fluctuate
    # completely normally cycle to cycle (a company simply not posting a
    # matching role in the last few hours is not the same as its ATS
    # integration being broken). The stale-company health check (see
    # scrape_watchlist()) needs the former signal, not the latter — using
    # len(jobs) there was flooding false "may be stale" alerts for
    # companies that were working fine, just quiet that cycle.
    return jobs, len(postings)


def fetch_amazon(role):
    try:
        resp = requests.get(
            "https://www.amazon.jobs/en/search.json",
            params={"query": role, "country": "IND", "result_limit": 30},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Amazon jobs fetch failed for role=%s: %s", role, e)
        return []
    jobs = []
    for j in resp.json().get("jobs", []):
        location = ", ".join(filter(None, [j.get("city"), j.get("state")]))
        description = " ".join(filter(None, [
            j.get("description"), j.get("basic_qualifications"), j.get("preferred_qualifications"),
        ]))
        jobs.append(_raw(
            j.get("title"), "Amazon", location, description,
            "https://www.amazon.jobs" + (j.get("job_path") or ""), "amazon",
            age_hours=_amazon_age_hours(j.get("posted_date")),
        ))
    return jobs


# Consecutive zero-raw-result cycles before flagging a watchlist company as
# newly stale — i.e. its API returned a genuine response but with 0 postings
# at all, not "0 postings matching our roles/locations". Confirmed this
# actually happens in practice: Dell migrated off Workday to Oracle Fusion
# Cloud mid-project and would otherwise go silently, permanently stale —
# nothing raises an exception, it just quietly contributes nothing forever.
WATCHLIST_STALE_THRESHOLD = 5


def _track_company_health(company, raw_count):
    """Returns True the cycle a company first crosses
    WATCHLIST_STALE_THRESHOLD consecutive zero-result runs."""
    import database  # deferred: keeps career_sites.py's non-DB functions importable standalone
    key = f"watchlist_zero_streak:{company}"
    if raw_count > 0:
        database.set_meta(key, 0)
        return False
    streak = int(database.get_meta(key, 0) or 0) + 1
    database.set_meta(key, streak)
    return streak == WATCHLIST_STALE_THRESHOLD


def scrape_watchlist(roles, hours_old=None):
    """Sweep every configured Greenhouse/Lever/Workday company plus Amazon's
    own jobs API. Runs synchronously (plain HTTP, no browser) — call via
    asyncio.to_thread() from pipeline.sweep() like naukri_playwright is.

    hours_old filters by each source's own posting-date field (Greenhouse
    first_published, Lever createdAt, Workday's "Posted N Days Ago" text,
    Amazon's posted_date) — confirmed live these are all actually available,
    despite this module originally assuming otherwise and skipping age
    filtering entirely. That gap meant every company's full current listing
    (regardless of how old) looked "new" the first time jobs.db ever saw it,
    which is exactly what happened while jobs.db wasn't persisting (see
    pipeline.py/the workflow git-push fix) — postings 3-5+ days old kept
    surfacing as if fresh. A job whose age can't be determined (parse
    failure, missing field) is kept rather than dropped, to avoid hiding a
    genuinely fresh posting over a parsing edge case.

    Returns (jobs, newly_stale_companies) — the second element is normally
    empty; when non-empty, the caller should surface it (see
    cloud_run.notify_stale_watchlist_companies())."""
    from pipeline import role_matches  # deferred: pipeline imports this module

    jobs, newly_stale = [], []

    def track(company, company_jobs):
        jobs.extend(company_jobs)
        if _track_company_health(company, len(company_jobs)):
            newly_stale.append(company)

    for company, slug in GREENHOUSE_COMPANIES.items():
        track(company, fetch_greenhouse(company, slug))
    for company, slug in LEVER_COMPANIES.items():
        track(company, fetch_lever(company, slug))

    # Hard wall-clock budget for the whole Workday phase, independent of
    # per-call timeouts. Confirmed live: a full 22-company run took 4.4
    # HOURS (a single curl_cffi call alone hung 195s despite a configured
    # 20s timeout) — with 22 companies this project scale, even bounded
    # per-call timeouts compound to something that could still blow past
    # GitHub Actions' 40-minute job limit and fail the ENTIRE scheduled run
    # (all sources, not just watchlist) if several companies are flaky at
    # once. Once the budget's spent, remaining companies are skipped for
    # this run — they'll get picked up next cycle instead.
    #
    # Iterating WORKDAY_COMPANIES in the same fixed dict order every cycle
    # meant that whenever the budget ran tight, it was always the SAME
    # tail-end companies (currently several of the strongest additions —
    # Accenture, Genpact, Cisco) that got sacrificed, cycle after cycle,
    # while the same front-of-list companies always got processed. Rotating
    # the starting point each cycle (persisted via database meta, same
    # pattern as the stale-company streak) spreads that risk evenly instead
    # of permanently disadvantaging whoever happens to be listed last.
    import database  # deferred: keeps career_sites.py's non-DB functions importable standalone
    all_workday = list(WORKDAY_COMPANIES.items())
    offset = int(database.get_meta("workday_rotation_offset", 0) or 0) % len(all_workday)
    rotated = all_workday[offset:] + all_workday[:offset]

    workday_deadline = time.time() + WORKDAY_BUDGET_SECONDS
    skipped = []
    attempted = 0
    for company, cfg in rotated:
        if time.time() >= workday_deadline:
            skipped.append(company)
            continue
        attempted += 1
        # Not health-tracked when skipped for budget — that's a resource
        # constraint, not the company itself being broken. Uses the raw
        # postings count for health, not len(company_jobs) — see
        # fetch_workday()'s return docstring for why the two must be kept
        # separate (confirmed live: conflating them flooded false "may be
        # stale" alerts for companies working perfectly fine, just without
        # a role-matched AND recent-enough posting that particular cycle).
        company_jobs, raw_count = fetch_workday(
            company, cfg["tenant"], cfg["host"], cfg["site"], roles, role_matches, hours_old)
        jobs.extend(company_jobs)
        if _track_company_health(company, raw_count):
            newly_stale.append(company)
    database.set_meta("workday_rotation_offset", (offset + attempted) % len(all_workday))
    if skipped:
        logger.warning("Workday budget (%ds) exhausted — skipped %d companies this run: %s",
                        WORKDAY_BUDGET_SECONDS, len(skipped), ", ".join(skipped))

    for role in roles:
        jobs.extend(fetch_amazon(role))

    total_fetched = len(jobs)
    jobs = [j for j in jobs if _is_india_relevant(j["location"])]

    if hours_old is not None:
        before_age_filter = len(jobs)
        jobs = [j for j in jobs if j["age_hours"] is None or j["age_hours"] <= hours_old]
        logger.info("Age filter (hours_old=%s): %d -> %d postings.", hours_old, before_age_filter, len(jobs))

    logger.info("Watchlist sweep fetched %d raw postings across %d companies, %d India-relevant.",
                total_fetched, len(GREENHOUSE_COMPANIES) + len(LEVER_COMPANIES) + len(WORKDAY_COMPANIES) + 1,
                len(jobs))
    if newly_stale:
        logger.warning("Watchlist companies newly stale (%d+ zero-result cycles): %s",
                        WATCHLIST_STALE_THRESHOLD, ", ".join(newly_stale))
    return jobs, newly_stale
