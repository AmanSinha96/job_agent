"""
naukri_playwright.py — Naukri scraping via a real browser (Playwright + stealth).

jobspy's built-in Naukri scraper hits naukri.com/jobapi/v3/search directly and
gets a hard 406 "recaptcha required" every time (confirmed live, TLS-fingerprint
spoofing and header tuning both failed to get past it). A real rendered browser
hitting the public HTML search page gets through most of the time instead —
confirmed via repeated live testing, including from an actual GitHub Actions
runner: consistently ~3/4 requests succeed, ~1/4 hit an intermittent 403.

That failure rate is NOT fixable by this module — every combo is wrapped so a
blocked request just gets skipped, exactly like a jobspy site returning zero
rows. Do not remove the per-combo try/except thinking you're "fixing" flakiness;
the flakiness is inherent to scraping Naukri at all right now.
"""

from __future__ import annotations

import logging
import re
import time
import random

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.naukri.com/{role_slug}-jobs-in-{city_slug}"
JOB_CARD_SELECTOR = ".srp-jobtuple-wrapper"
BLOCK_SIGNALS = ["recaptcha", "access denied", "unusual traffic", "are you a human"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _parse_posted_hours_ago(text: str) -> int:
    """Naukri shows relative freshness ('2 days ago', '3+ weeks ago') not a
    timestamp. Best-effort parse to hours; unknown formats are treated as
    stale (large value) so they get filtered out by hours_old rather than
    silently included."""
    text = (text or "").strip().lower()
    if not text:
        return 24 * 365
    if "just now" in text or "today" in text or "few hours" in text:
        return 1
    m = re.search(r"(\d+)\+?\s*hour", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\+?\s*day", text)
    if m:
        return int(m.group(1)) * 24
    m = re.search(r"(\d+)\+?\s*week", text)
    if m:
        return int(m.group(1)) * 24 * 7
    m = re.search(r"(\d+)\+?\s*month", text)
    if m:
        return int(m.group(1)) * 24 * 30
    return 24 * 365


def _extract_text(card, selector: str) -> str:
    loc = card.locator(selector)
    if loc.count() == 0:
        return ""
    return (loc.first.get_attribute("title") or loc.first.inner_text() or "").strip()


def _parse_card(card, city: str) -> dict | None:
    title_el = card.locator("a.title")
    if title_el.count() == 0:
        return None

    title = (title_el.first.get_attribute("title") or title_el.first.inner_text() or "").strip()
    url = title_el.first.get_attribute("href") or ""
    if not title or not url:
        return None

    company = _extract_text(card, "a.comp-name")
    location = _extract_text(card, ".locWdth") or city
    salary = _extract_text(card, ".sal-wrap .sal") or None
    snippet = _extract_text(card, ".job-desc")
    tags = card.locator(".tags-gt .tag-li").all_inner_texts()
    posted_text = _extract_text(card, ".job-post-day")

    description = snippet
    if tags:
        description = f"{snippet} Skills: {', '.join(t.strip() for t in tags if t.strip())}"

    return {
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "url": url,
        "salary": salary,
        "board": "naukri",
        "company_size": 0,  # not shown on the search card
        "hours_ago": _parse_posted_hours_ago(posted_text),
    }


def scrape_naukri(roles: list[str], locations: list[str], hours_old: int) -> list[dict]:
    """Scrape Naukri for each (role, city) combo. Returns raw job dicts in the
    same shape pipeline._jobspy_row_to_raw() produces, plus an 'hours_ago' key
    the caller should use to apply the hours_old cutoff (Naukri gives no
    server-side freshness filter, unlike jobspy's other sources)."""
    results: list[dict] = []

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True)
        try:
            for role in roles:
                role_slug = _slugify(role)
                for city in locations:
                    city_slug = _slugify(city)
                    url = SEARCH_URL.format(role_slug=role_slug, city_slug=city_slug)

                    context = browser.new_context(
                        user_agent=USER_AGENT,
                        viewport={"width": 1366, "height": 900},
                        locale="en-IN",
                    )
                    page = context.new_page()
                    try:
                        resp = page.goto(url, wait_until="networkidle", timeout=30000)
                        time.sleep(3)

                        body = page.inner_text("body").lower()
                        if any(s in body for s in BLOCK_SIGNALS) or (resp and resp.status >= 400):
                            logger.warning(
                                "Naukri blocked for role=%s city=%s (status=%s) — skipping",
                                role, city, resp.status if resp else "?",
                            )
                            continue

                        cards = page.locator(JOB_CARD_SELECTOR)
                        count = cards.count()
                        for i in range(count):
                            job = _parse_card(cards.nth(i), city)
                            if job and job["hours_ago"] <= hours_old:
                                results.append(job)

                    except Exception as e:
                        logger.error("Naukri scrape error for role=%s city=%s: %s", role, city, e)
                    finally:
                        context.close()
                        time.sleep(random.uniform(5, 9))
        finally:
            browser.close()

    logger.info("Naukri (Playwright): %d jobs within hours_old=%d across %d combos",
                len(results), hours_old, len(roles) * len(locations))
    return results
