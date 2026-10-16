"""
job_boards.py — free public job-board APIs, the "remote_boards" source.

Complements the aggregators (LinkedIn/Indeed/Glassdoor via jobspy, Naukri via
Playwright) and the company watchlist (career_sites.py) with remote roles
that are explicitly open to candidates in India — these mostly come from
remote-first companies that don't post on Indian boards at all.

Himalayas (himalayas.app) — public JSON search API, no auth, supports a
country-eligibility filter and newest-first sorting, with a real posting
timestamp per job. Confirmed live 2026-10: ~470 India-eligible results for
"data analyst", newest a few hours old.

Evaluated 2026-10 and NOT added:
- Remotive: works, but ~10 India-eligible results per query, almost none
  relevant to these roles.
- Jobicy: no India geo — results are USA/EMEA/LATAM-restricted.
- Instahyre: public API works, but results aren't date-ordered and carry no
  posted date, so new postings can't be told apart from months-old ones;
  also rate-limits quickly.
- Foundit (Monster India): middleware search API rejects non-browser
  clients (HTTP 400).
"""
import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"}
_TIMEOUT = 20

HIMALAYAS_SEARCH_URL = "https://himalayas.app/jobs/api/search"
# 20 results/page, newest first — 3 pages comfortably covers a 48h window
# per role at the volumes seen live.
HIMALAYAS_MAX_PAGES = 3


def _age_hours(epoch_seconds):
    try:
        published = datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return (datetime.now(timezone.utc) - published).total_seconds() / 3600


def fetch_himalayas(role: str, hours_old: int) -> list[dict]:
    jobs = []
    for page in range(1, HIMALAYAS_MAX_PAGES + 1):
        try:
            resp = requests.get(
                HIMALAYAS_SEARCH_URL,
                params={"q": role, "country": "India", "sort": "recent", "page": page},
                headers=_HEADERS, timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning("Himalayas fetch failed for role=%s page=%d: %s", role, page, e)
            break
        reached_older = False
        for j in results:
            age = _age_hours(j.get("pubDate"))
            if age is not None and age > hours_old:
                reached_older = True
                continue
            jobs.append({
                "title": j.get("title") or "",
                "company": j.get("companyName") or "",
                "location": "Remote",
                "description": j.get("description") or j.get("excerpt") or "",
                "url": j.get("applicationLink") or j.get("guid") or "",
                "salary": None,
                "board": "himalayas",
                "company_size": 0,
                "age_hours": age,
            })
        if reached_older or len(results) < 20:
            break
        time.sleep(1)
    return jobs


def scrape_remote_boards(roles: list[str], hours_old: int) -> list[dict]:
    """Synchronous (plain HTTP) — call via asyncio.to_thread() from
    pipeline.sweep(). Returns raw job dicts in the same shape as the other
    sources; HTML descriptions are fine, shared_utils/should_keep only do
    keyword matching on them."""
    from bs4 import BeautifulSoup

    jobs = []
    for role in roles:
        jobs.extend(fetch_himalayas(role, hours_old))
        time.sleep(1)
    for j in jobs:
        if "<" in j["description"]:
            j["description"] = BeautifulSoup(j["description"], "lxml").get_text(" ", strip=True)
    logger.info("Remote job boards: %d postings within hours_old=%d across %d roles",
                len(jobs), hours_old, len(roles))
    return jobs
