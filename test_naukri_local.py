"""
test_naukri_local.py — one-off diagnostic script, NOT part of the pipeline.

Run this LOCALLY on your own machine/residential connection to check whether
Naukri's block is IP-reputation-based (confirmed likely, since TLS-fingerprint
spoofing and header tuning both failed identically from a datacenter IP) —
not something to run from GitHub Actions or any cloud/VPS box, since that
defeats the entire point of the test.

Setup (run once):
    pip install playwright playwright-stealth
    playwright install chromium

Run:
    python test_naukri_local.py
"""

import sys
import time

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

SEARCH_URL = "https://www.naukri.com/data-analyst-jobs-in-bangalore"

BLOCK_SIGNALS = ["recaptcha", "access denied", "unusual traffic", "are you a human"]

# A few historical Naukri job-card selectors — sites change markup over time,
# so this checks several rather than betting on exactly one.
JOB_CARD_SELECTORS = [
    ".srp-jobtuple-wrapper",
    ".jobTuple",
    "[data-job-id]",
    "article.jobTuple",
]


def main():
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=False)  # visible on purpose — watch what happens
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-IN",
        )
        page = context.new_page()

        print(f"Navigating to {SEARCH_URL} ...")
        response = page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"HTTP status: {response.status if response else 'unknown'}")

        # Give any client-side challenge/render a moment to settle.
        time.sleep(4)

        page.screenshot(path="naukri_test_screenshot.png", full_page=True)
        print("Saved naukri_test_screenshot.png — open it to see exactly what loaded.")

        body_text = page.inner_text("body").lower()
        hit_signals = [s for s in BLOCK_SIGNALS if s in body_text]

        job_count = 0
        matched_selector = None
        for sel in JOB_CARD_SELECTORS:
            count = page.locator(sel).count()
            if count > 0:
                job_count = count
                matched_selector = sel
                break

        print()
        print("=" * 60)
        if hit_signals:
            print(f"BLOCKED — page contains: {hit_signals}")
            print("Verdict: still getting challenged, even from this connection.")
        elif job_count > 0:
            print(f"SUCCESS — found {job_count} job card elements via selector '{matched_selector}'")
            print("Sample titles:")
            titles = page.locator(matched_selector).all_inner_texts()[:3]
            for t in titles:
                print(" -", " ".join(t.split())[:100])
            print("Verdict: real job data loaded. IP-reputation theory confirmed —")
            print("this connection is NOT being blocked the way the sandbox/CI IP was.")
        else:
            print("INCONCLUSIVE — no block signal found, but no job cards matched either.")
            print("Naukri may have changed its markup, or the page needs more time to render.")
            print("Check naukri_test_screenshot.png to see what actually loaded.")
        print("=" * 60)

        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Script error: {type(e).__name__}: {e}", file=sys.stderr)
        raise
