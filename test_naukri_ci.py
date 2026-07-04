"""
test_naukri_ci.py — one-off diagnostic script for GitHub Actions, NOT part of
the pipeline. Companion to test_naukri_local.py, adapted for a headless
CI runner (no display, so headless=True; and networkidle wait since job
cards render asynchronously via JS after the page technically loads).

Run only via the test_naukri_playwright.yml workflow's workflow_dispatch —
this is a diagnostic, not something to schedule.
"""

import sys
import time
import random

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

URLS = [
    "https://www.naukri.com/data-analyst-jobs-in-bangalore",
    "https://www.naukri.com/data-engineer-jobs-in-pune",
    "https://www.naukri.com/analytics-engineer-jobs-in-hyderabad",
    "https://www.naukri.com/ai-engineer-jobs-in-gurugram",
]

BLOCK_SIGNALS = ["recaptcha", "access denied", "unusual traffic", "are you a human"]
JOB_CARD_SELECTORS = [".srp-jobtuple-wrapper", ".jobTuple", "[data-job-id]", "article.jobTuple"]


def main():
    results = []
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True)
        for i, url in enumerate(URLS):
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 900},
                locale="en-IN",
            )
            page = context.new_page()
            try:
                resp = page.goto(url, wait_until="networkidle", timeout=30000)
                status = resp.status if resp else None
            except Exception as e:
                status = f"error: {e}"

            time.sleep(3)
            body = page.inner_text("body").lower()
            hits = [s for s in BLOCK_SIGNALS if s in body]

            job_count, matched = 0, None
            for sel in JOB_CARD_SELECTORS:
                c = page.locator(sel).count()
                if c > 0:
                    job_count, matched = c, sel
                    break

            page.screenshot(path=f"naukri_ci_test_{i+1}.png", full_page=True)
            results.append((url, status, job_count, hits))
            print(f"[{i+1}/{len(URLS)}] {url} -> status={status} jobs={job_count} "
                  f"selector={matched} block_signals={hits}")

            context.close()
            time.sleep(random.uniform(5, 9))
        browser.close()

    successes = sum(1 for _, status, jobs, hits in results if status == 200 and jobs > 0 and not hits)
    print()
    print("=" * 60)
    print(f"SUMMARY: {successes}/{len(results)} requests succeeded with real job data")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Script error: {type(e).__name__}: {e}", file=sys.stderr)
        raise
