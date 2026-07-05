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
import logging
import requests
from curl_cffi import requests as curl_requests

from job_filters import LOCATIONS

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 20

# Confirmed live 2026-07 — each slug actually returns HTTP 200 with real
# postings (see conversation for the verification curls). Add more by
# checking https://boards-api.greenhouse.io/v1/boards/{slug}/jobs first.
GREENHOUSE_COMPANIES = {
    "Groww": "groww",
    "Postman": "postman",
    "Stripe": "stripe",
    "Databricks": "databricks",
    "Twilio": "twilio",
}

# Confirmed live 2026-07 — check https://api.lever.co/v0/postings/{slug}?mode=json first.
LEVER_COMPANIES = {
    "CRED": "cred",
    "Meesho": "meesho",
    "Freshworks": "freshworks",
    "Zeta": "zeta",
}

# Confirmed live 2026-07: fractal.ai/careers redirects to fractal.wd1.myworkdayjobs.com/Careers.
# Commonwealth Bank of Australia has a genuine Bangalore tech/engineering hub
# (35 India-tagged postings incl. "Staff Data Engineer" confirmed live) —
# tenant/host/site found via the myworkdayjobs.com links embedded in
# commbank.com.au/about-us/careers.html.
WORKDAY_COMPANIES = {
    "Fractal": {"tenant": "fractal", "host": "wd1", "site": "Careers"},
    "Commonwealth Bank": {"tenant": "cba", "host": "wd3", "site": "CommBank_Careers"},
}

# Approximate headcount for company_size_bonus() (see job_filters.py) —
# jobspy-sourced jobs get this from Indeed/LinkedIn's own data; these
# watchlist sources don't return it, so it's hardcoded per known company.
COMPANY_SIZE_HINTS = {
    "Fractal": 5000, "Groww": 1500, "Postman": 600, "Stripe": 8000,
    "Databricks": 7000, "Twilio": 5000, "CRED": 2000, "Meesho": 5000,
    "Freshworks": 5000, "Zeta": 3000, "Amazon": 1500000, "Commonwealth Bank": 50000,
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


def _raw(title, company, location, description, url, board):
    return {
        "title": title or "",
        "company": company,
        "location": _normalize_location(location),
        "description": description or "",
        "url": url or "",
        "salary": None,
        "board": board,
        "company_size": COMPANY_SIZE_HINTS.get(company, 0),
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
             _strip_html(j.get("content")), j.get("absolute_url"), "greenhouse")
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
        ))
    return jobs


def fetch_workday(company, tenant, host, site, role_filter):
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

    def fetch_page(offset):
        for attempt in range(3):
            try:
                # Page size of 20 is confirmed safe across tenants — some
                # (Commonwealth Bank's "cba" tenant) hard-reject limit=50
                # with a 400 even though Fractal's tenant accepts it fine.
                resp = curl_requests.post(
                    f"{base}/wday/cxs/{tenant}/{site}/jobs",
                    json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
                    impersonate="chrome", timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.warning("Workday fetch attempt %d failed for %s (offset=%d): %s",
                                attempt + 1, company, offset, e)
        return None

    # Paginate through the full listing (capped — a huge employer's board
    # isn't worth 500 HTTP calls just to find a handful of relevant titles).
    postings = []
    offset = 0
    max_postings = 300
    while offset < max_postings:
        page = fetch_page(offset)
        if page is None:
            break
        batch = page.get("jobPostings", [])
        if not batch:
            break
        postings.extend(batch)
        # "total" came back as 0 on a later page of a real, populated
        # listing (CBA's tenant, confirmed live) despite jobPostings being
        # non-empty — unreliable, so an empty batch is the only trustworthy
        # stop condition; max_postings is the backstop against a bad company
        # whose "total" is permanently wrong.
        offset += len(batch)

    jobs = []
    for p in postings:
        title = p.get("title", "")
        # Skip the per-job detail fetch (a separate HTTP call) unless the
        # title alone could plausibly pass role_matches() later — most of a
        # large employer's postings are irrelevant, no point fetching all.
        if not role_filter(title):
            continue
        path = p.get("externalPath", "")
        description = ""
        try:
            # API detail path (returns JSON) differs from the public browsable
            # URL below — confirmed live: /wday/cxs/{tenant}/{site}{path}.
            detail = curl_requests.get(f"{base}/wday/cxs/{tenant}/{site}{path}",
                                        impersonate="chrome", timeout=_TIMEOUT)
            detail.raise_for_status()
            description = _strip_html(detail.json().get("jobPostingInfo", {}).get("jobDescription", ""))
        except Exception as e:
            logger.warning("Workday detail fetch failed for %s %s: %s", company, title, e)
        jobs.append(_raw(title, company, p.get("locationsText", ""), description,
                          f"{base}/{site}{path}", "workday"))
    return jobs


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
        ))
    return jobs


def scrape_watchlist(roles):
    """Sweep every configured Greenhouse/Lever/Workday company plus Amazon's
    own jobs API. Runs synchronously (plain HTTP, no browser) — call via
    asyncio.to_thread() from pipeline.sweep() like naukri_playwright is.

    No hours_old filtering: these APIs don't reliably expose posting age,
    and the watchlist is small/curated enough that re-fetching everything
    each run and relying on the existing already_exists() URL dedup in
    pipeline.sweep() is simpler than building per-source staleness logic."""
    from pipeline import role_matches  # deferred: pipeline imports this module

    jobs = []
    for company, slug in GREENHOUSE_COMPANIES.items():
        jobs.extend(fetch_greenhouse(company, slug))
    for company, slug in LEVER_COMPANIES.items():
        jobs.extend(fetch_lever(company, slug))
    for company, cfg in WORKDAY_COMPANIES.items():
        jobs.extend(fetch_workday(company, cfg["tenant"], cfg["host"], cfg["site"], role_matches))
    for role in roles:
        jobs.extend(fetch_amazon(role))

    total_fetched = len(jobs)
    jobs = [j for j in jobs if _is_india_relevant(j["location"])]

    logger.info("Watchlist sweep fetched %d raw postings across %d companies, %d India-relevant.",
                total_fetched, len(GREENHOUSE_COMPANIES) + len(LEVER_COMPANIES) + len(WORKDAY_COMPANIES) + 1,
                len(jobs))
    return jobs
