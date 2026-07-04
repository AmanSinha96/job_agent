"""
applier.py — Multi-ATS form filler
Handles: Workday, Greenhouse, Lever, Naukri, LinkedIn Easy Apply, Indeed
Covers: all mandatory fields, consent checkboxes, multi-page forms,
        Groq-powered custom screening questions, CAPTCHA, Gmail OTP.
"""

import asyncio
import json
import logging
import random
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from groq import Groq
from playwright.async_api import async_playwright, Page, BrowserContext

from config import (
    DB_PATH,
    BASE_DIR,
    DEFAULT_ANSWERS,
    NOPECHA_API_KEY,
    GROQ_API_KEY,
    GROQ_MODEL,
    MIN_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
    MIN_APPLY_DELAY,
    MAX_APPLY_DELAY,
)

# Note: asyncio and random imports moved to top

logger = logging.getLogger(__name__)

async def random_delay(min_s=0.8, max_s=2.0):
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_type(page, selector, text):
    await page.click(selector)

    for ch in str(text):
        await page.keyboard.type(ch)
        await asyncio.sleep(random.uniform(0.04, 0.12))

SCREENSHOT_DIR = BASE_DIR / "screenshots"
Path(SCREENSHOT_DIR).mkdir(exist_ok=True)

_groq = None
def get_groq():
    global _groq
    if _groq is None and GROQ_API_KEY:
        _groq = Groq(api_key=GROQ_API_KEY)
    return _groq


# ── Result object ──────────────────────────────────────────────────────────────

class ApplyResult:
    def __init__(self, success: bool, reason: str = "", screenshot: str = ""):
        self.success    = success
        self.reason     = reason
        self.screenshot = screenshot


# ── Groq: answer custom screening questions ────────────────────────────────────

def answer_screening_question(question: str, job_title: str, company: str) -> str:
    """
    Use Groq to generate a concise, honest answer to a custom screening question.
    Falls back to generic answers if Groq unavailable.
    """
    ans = DEFAULT_ANSWERS

    # Handle common binary yes/no questions without calling Groq
    q_lower = question.lower()
    if any(w in q_lower for w in ["authorized", "eligible", "legally allowed"]):
        return "Yes"
    if any(w in q_lower for w in ["require sponsor", "need visa", "work permit"]):
        return "No"
    if any(w in q_lower for w in ["relocat"]):
        return "Yes, I am willing to relocate."
    if any(w in q_lower for w in ["notice period", "how soon", "when can you start"]):
        return f"I can join within {ans['notice_period']}."
    if any(w in q_lower for w in ["current salary", "ctc"]):
        return f"{ans['current_salary_lpa']} LPA"
    if any(w in q_lower for w in ["expected salary", "salary expectation"]):
        return f"{ans['salary_display']}"
    if any(w in q_lower for w in ["years of experience", "how many years"]):
        return ans["years_experience"]
    if any(w in q_lower for w in ["why do you want", "why this company", "why us"]):
        return ans["generic_why_company"].format(
            company=company, job_title=job_title
        )

    # Use Groq for everything else
    client = get_groq()
    if not client:
        return ans["generic_experience_answer"]

    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.3,
            max_tokens=150,
            messages=[{
                "role": "system",
                "content": (
                    "You are filling out a job application form for Aman Sinha, "
                    "a Senior Data Consultant with 6+ years experience in data engineering, "
                    "analytics, and AI products. Answer screening questions concisely and honestly. "
                    "Never fabricate experience. Return only the answer text, no preamble."
                )
            }, {
                "role": "user",
                "content": (
                    f"Job: {job_title} at {company}\n"
                    f"Question: {question}\n"
                    "Answer (1-3 sentences max):"
                )
            }]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq screening answer failed: {e}")
        return ans["generic_experience_answer"]


# ── NopeCHA CAPTCHA solver ─────────────────────────────────────────────────────

async def solve_recaptcha_v2(page: Page, site_key: str, page_url: str) -> Optional[str]:
    if not NOPECHA_API_KEY:
        return None
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.nopecha.com/",
            json={"key": NOPECHA_API_KEY, "type": "recaptchav2",
                  "sitekey": site_key, "url": page_url},
            timeout=15,
        )
        data = resp.json()
        if data.get("error"):
            logger.error(f"NopeCHA error: {data}")
            return None
        task_id = data.get("data")
        for _ in range(30):
            await asyncio.sleep(3)
            poll = await client.get(
                "https://api.nopecha.com/",
                params={"key": NOPECHA_API_KEY, "id": task_id}, timeout=10,
            )
            pd = poll.json()
            if pd.get("data") and isinstance(pd["data"], list):
                return pd["data"][0]
            if pd.get("error"):
                return None
    return None


async def handle_captcha_if_present(page: Page) -> tuple[bool, str]:
    try:
        frame = await page.query_selector("iframe[src*='recaptcha']")
        if not frame:
            return True, ""
        if not NOPECHA_API_KEY:
            return False, "captcha_unsolvable_no_api_key"
        src   = await frame.get_attribute("src") or ""
        match = re.search(r"[?&]k=([A-Za-z0-9_-]+)", src)
        if not match:
            return False, "captcha_sitekey_not_found"
        token = await solve_recaptcha_v2(page, match.group(1), page.url)
        if token:
            await page.evaluate(f"""
                document.getElementById('g-recaptcha-response').innerHTML = '{token}';
                if (typeof ___grecaptcha_cfg !== 'undefined') {{
                    Object.entries(___grecaptcha_cfg.clients).forEach(([k,v]) => {{
                        if (v && v.callback) v.callback('{token}');
                    }});
                }}
            """)
            await random_delay(1, 2)
            return True, ""
        return False, "captcha_unsolvable"
    except Exception as e:
        return False, f"captcha_error:{e}"


# ── Gmail OTP reader ───────────────────────────────────────────────────────────

def fetch_otp_from_gmail(sender_filter: str = "", max_wait: int = 60) -> Optional[str]:
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from config import GMAIL_CREDENTIALS_FILE, GMAIL_TOKEN_FILE, GMAIL_SCOPES

        creds = None
        if GMAIL_TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_FILE), GMAIL_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(GMAIL_CREDENTIALS_FILE), GMAIL_SCOPES)
                creds = flow.run_local_server(port=0)
            GMAIL_TOKEN_FILE.write_text(creds.to_json())

        service  = build("gmail", "v1", credentials=creds)
        query    = f"is:unread newer_than:2m {f'from:{sender_filter}' if sender_filter else ''}"
        deadline = time.time() + max_wait
        time.sleep(15)

        while time.time() < deadline:
            results  = service.users().messages().list(userId="me", q=query, maxResults=5).execute()
            for msg in results.get("messages", []):
                full    = service.users().messages().get(userId="me", id=msg["id"], format="snippet").execute()
                snippet = full.get("snippet", "")
                match   = re.search(r"(?<!\d)(\d{4,8})(?!\d)", snippet)
                if match:
                    otp = match.group(1)
                    logger.info(f"OTP found: {otp[:2]}****")
                    service.users().messages().modify(
                        userId="me", id=msg["id"], body={"removeLabelIds": ["UNREAD"]}
                    ).execute()
                    return otp
            time.sleep(5)
        return None
    except Exception as e:
        logger.error(f"Gmail OTP error: {e}")
        return None


# ── Universal form-filling helpers ─────────────────────────────────────────────

async def fill_text(page: Page, selectors: list, value: str) -> bool:
    """Try each selector; fill the first found. Returns True if filled."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.scroll_into_view_if_needed()
                await el.triple_click()
                await el.fill(value)
                return True
        except Exception:
            continue
    return False


async def select_option(page: Page, selectors: list, value: str) -> bool:
    """Try to select a dropdown option by label, value, or partial text match."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                # Try direct select_option first
                try:
                    await page.select_option(sel, label=value)
                    return True
                except Exception:
                    pass
                # Try selecting by value
                try:
                    await page.select_option(sel, value=value)
                    return True
                except Exception:
                    pass
                # Try clicking the dropdown then finding matching option
                await el.click()
                await random_delay(0.5, 1)
                options = await page.query_selector_all(f"{sel} option")
                for opt in options:
                    text = (await opt.inner_text()).strip().lower()
                    if value.lower() in text or text in value.lower():
                        await opt.click()
                        return True
        except Exception:
            continue
    return False


async def check_checkbox(page: Page, selectors: list) -> bool:
    """Check a checkbox if it's not already checked."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                checked = await el.is_checked()
                if not checked:
                    await el.check()
                return True
        except Exception:
            continue
    return False


async def click_button(page: Page, selectors: list) -> bool:
    """Click the first visible, enabled button matching any selector."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible() and await el.is_enabled():
                await el.scroll_into_view_if_needed()
                await el.click()
                return True
        except Exception:
            continue
    return False


async def upload_file(page: Page, selectors: list, file_path: Optional[str]) -> bool:
    """Upload a file to the first matching file input."""
    if not file_path or not Path(file_path).exists():
        return False
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.set_input_files(file_path)
                await random_delay(1, 2)
                return True
        except Exception:
            continue
    return False


async def fill_all_consent_checkboxes(page: Page):
    """Find and check all unchecked consent/agreement/privacy checkboxes on the page."""
    consent_patterns = [
        "input[type='checkbox'][name*='agree']",
        "input[type='checkbox'][name*='consent']",
        "input[type='checkbox'][name*='privacy']",
        "input[type='checkbox'][name*='terms']",
        "input[type='checkbox'][name*='gdpr']",
        "input[type='checkbox'][id*='agree']",
        "input[type='checkbox'][id*='consent']",
        "input[type='checkbox'][id*='privacy']",
        "input[type='checkbox'][id*='terms']",
        "input[type='checkbox'][aria-label*='agree']",
        "input[type='checkbox'][aria-label*='consent']",
    ]
    for pattern in consent_patterns:
        try:
            boxes = await page.query_selector_all(pattern)
            for box in boxes:
                if await box.is_visible() and not await box.is_checked():
                    await box.check()
                    await random_delay(0.3, 0.7)
        except Exception:
            continue


async def handle_screening_questions(page: Page, job: dict):
    """
    Find open-text screening question fields and fill them using Groq.
    Targets common patterns: textarea, input[type=text] inside question containers.
    """
    title   = job.get("title", "")
    company = job.get("company", "")

    # Common containers for screening questions
    question_containers = await page.query_selector_all(
        "div[data-automation*='question'], "
        "div.application-question, "
        "div[class*='screening'], "
        "fieldset[data-testid*='question']"
    )

    for container in question_containers:
        try:
            # Get the question label text
            label_el = await container.query_selector("label, legend, p, span[class*='label']")
            if not label_el:
                continue
            question_text = (await label_el.inner_text()).strip()
            if len(question_text) < 5:
                continue

            # Find the input/textarea inside this container
            input_el = await container.query_selector("textarea, input[type='text']")
            if not input_el:
                continue

            # Skip if already filled
            current_val = await input_el.input_value()
            if current_val.strip():
                continue

            # Get answer from Groq
            answer = answer_screening_question(question_text, title, company)
            await input_el.scroll_into_view_if_needed()
            await input_el.fill(answer)
            logger.info(f"Answered screening: '{question_text[:60]}...'")
            await random_delay(0.5, 1.5)

        except Exception as e:
            logger.debug(f"Screening question handler error: {e}")


async def save_screenshot(page: Page, id: str, tag: str) -> str:
    path = str(SCREENSHOT_DIR / f"{id}_{tag}.png")
    try:
        await page.screenshot(path=path, full_page=True)
    except Exception:
        pass
    return path


# ── ATS detection ───────────────────────────────────────────────────────────────

def detect_ats(url: str) -> str:
    """
    Detect the ATS/vendor from the application URL.

    Returns one of:
        workday
        greenhouse
        lever
        linkedin
        naukri
        indeed
        smartrecruiters
        icims
        taleo
        ashby
        jobvite
        instahyre
        foundit
        generic_form
    """

    if not url:
        return "generic_form"

    url = url.lower()

    if "myworkdayjobs" in url or "workday" in url:
        return "workday"

    if "greenhouse.io" in url:
        return "greenhouse"

    if "lever.co" in url:
        return "lever"

    if "linkedin.com" in url:
        return "linkedin"

    if "naukri.com" in url:
        return "naukri"

    if "indeed.com" in url:
        return "indeed"

    if "smartrecruiters.com" in url:
        return "smartrecruiters"

    if "icims.com" in url:
        return "icims"

    if "taleo.net" in url:
        return "taleo"

    if "ashbyhq.com" in url:
        return "ashby"

    if "jobvite.com" in url:
        return "jobvite"

    if "instahyre.com" in url:
        return "instahyre"

    if "foundit.in" in url or "monsterindia.com" in url:
        return "foundit"

    return "generic_form"


# ── Universal field filler (called by every handler) ──────────────────────────

async def fill_standard_fields(page: Page, job: dict):
    """
    Fill every standard field we know about.
    Safe to call on any page — silently skips fields not present.
    """
    ans = DEFAULT_ANSWERS

    # Resume path compatibility
    pdf = (
    job.get("pdf_path")
    or job.get("tailored_resume")
    or ans.get("resume_path")
)

    if pdf:
      pdf = str(pdf)

    # ── Name ──────────────────────────────────────────────────────────────────
    await fill_text(page, [
        "input#first_name", "input[name='first_name']",
        "input[name='firstName']", "input[autocomplete='given-name']",
        "input[data-automation-id='legalNameSection_firstName']",
        "input[id*='firstName']", "input[placeholder*='First']",
    ], ans["first_name"])

    await fill_text(page, [
        "input#last_name", "input[name='last_name']",
        "input[name='lastName']", "input[autocomplete='family-name']",
        "input[data-automation-id='legalNameSection_lastName']",
        "input[id*='lastName']", "input[placeholder*='Last']",
    ], ans["last_name"])

    await fill_text(page, [
        "input[name='name']", "input[id='name']",
        "input[placeholder*='Full name']", "input[autocomplete='name']",
    ], ans["full_name"])

    # ── Contact ───────────────────────────────────────────────────────────────
    await fill_text(page, [
        "input#email", "input[name='email']", "input[type='email']",
        "input[autocomplete='email']", "input[id*='email']",
        "input[placeholder*='Email']",
    ], ans["email"])

    await fill_text(page, [
        "input#phone", "input[name='phone']", "input[type='tel']",
        "input[name='phoneNumber']", "input[id*='phone']",
        "input[autocomplete='tel']", "input[placeholder*='Phone']",
        "input[data-automation-id='phone']",
    ], ans["phone"])

    # ── Location & Full Address ────────────────────────────────────────────────
    await fill_text(page, [
        "input[name='location']", "input[id*='location']",
        "input[placeholder*='Location']", "input[placeholder*='Current Location']",
    ], ans["current_location"])

    await fill_text(page, [
        "input[name='city']", "input[id*='city']",
        "input[placeholder*='City']", "input[autocomplete='address-level2']",
        "input[data-automation-id*='city']",
    ], ans["current_city"])

    await fill_text(page, [
        "input[name='state']", "input[id*='state']",
        "input[placeholder*='State']", "input[autocomplete='address-level1']",
        "input[data-automation-id*='state']",
    ], ans["current_state"])

    await fill_text(page, [
        "input[name='country']", "input[id*='country']",
        "input[placeholder*='Country']", "input[autocomplete='country-name']",
    ], ans["current_country"])

    await fill_text(page, [
        "input[name='zip']", "input[name='zipCode']", "input[name='pincode']",
        "input[id*='zip']", "input[id*='pincode']", "input[id*='postal']",
        "input[placeholder*='PIN']", "input[placeholder*='Zip']",
        "input[placeholder*='Postal']", "input[autocomplete='postal-code']",
    ], ans["current_pincode"])

    await fill_text(page, [
        "input[name='address']", "input[name='addressLine1']",
        "input[id*='address1']", "input[id*='addressLine1']",
        "input[placeholder*='Address Line 1']", "input[placeholder*='Street']",
        "textarea[name='address']", "textarea[id*='address']",
    ], ans["address_line1"])

    await fill_text(page, [
        "input[name='addressLine2']", "input[id*='address2']",
        "input[id*='addressLine2']", "input[placeholder*='Address Line 2']",
        "input[placeholder*='Apartment']", "input[placeholder*='Area']",
    ], ans["address_line2"])

    await select_option(page, [
        "select[name='country']", "select[id*='country']",
        "select[data-automation-id*='country']",
    ], ans["current_country"])

    await select_option(page, [
        "select[name='state']", "select[id*='state']",
        "select[data-automation-id*='state']",
    ], ans["current_state"])

    # ── URLs ──────────────────────────────────────────────────────────────────
    await fill_text(page, [
        "input[name='linkedin']", "input[id*='linkedin']",
        "input[placeholder*='LinkedIn']",
    ], ans["linkedin_url"])

    await fill_text(page, [
        "input[name='github']", "input[id*='github']",
        "input[placeholder*='GitHub']",
    ], ans["github_url"])

    # ── Resume upload ─────────────────────────────────────────────────────────
    if pdf:
      await upload_file(
        page,
        [
            "input[type='file']",
            "input[name='resume']",
            "input[id*='resume']",
            "input[name*='resume']",
            "input[id*='upload']",
            "input[accept*='.pdf']",
        ],
        pdf,
      )

    # ── Experience ────────────────────────────────────────────────────────────
    await fill_text(page, [
        "input[name*='experience']", "input[id*='experience']",
        "input[placeholder*='Years of experience']",
    ], ans["years_experience"])

    await select_option(page, [
        "select[name*='experience']", "select[id*='experience']",
        "select[data-automation-id*='experience']",
    ], ans["years_experience"])

    # ── Salary ────────────────────────────────────────────────────────────────
    await fill_text(page, [
        "input[name*='salary']", "input[id*='salary']",
        "input[placeholder*='Expected salary']",
        "input[placeholder*='Salary expectation']",
    ], ans["expected_salary_lpa"])

    await fill_text(page, [
        "input[name*='currentSalary']", "input[id*='currentSalary']",
        "input[placeholder*='Current salary']", "input[placeholder*='Current CTC']",
    ], ans["current_salary_lpa"])

    # ── Notice period ─────────────────────────────────────────────────────────
    await fill_text(page, [
        "input[name*='notice']", "input[id*='notice']",
        "input[placeholder*='Notice period']",
    ], ans["notice_period"])

    await select_option(page, [
        "select[name*='notice']", "select[id*='notice']",
    ], ans["notice_period"])

    # ── Work authorization dropdowns ──────────────────────────────────────────
    await select_option(page, [
        "select[name*='authorized']", "select[id*='authorized']",
        "select[name*='workAuth']", "select[id*='workAuth']",
        "select[data-automation-id*='workAuth']",
    ], "Yes")

    await select_option(page, [
        "select[name*='sponsor']", "select[id*='sponsor']",
        "select[data-automation-id*='sponsor']",
    ], "No")

    # ── Willing to relocate ───────────────────────────────────────────────────
    await select_option(page, [
        "select[name*='relocat']", "select[id*='relocat']",
    ], "Yes")

    # ── Gender (diversity forms) ──────────────────────────────────────────────
    await select_option(page, [
        "select[name*='gender']", "select[id*='gender']",
        "select[data-automation-id*='gender']",
    ], ans["gender"])

    # ── Veteran status ────────────────────────────────────────────────────────
    await select_option(page, [
        "select[name*='veteran']", "select[id*='veteran']",
        "select[data-automation-id*='veteran']",
    ], ans["veteran_status_workday"])

    # ── Disability ────────────────────────────────────────────────────────────
    await select_option(page, [
        "select[name*='disab']", "select[id*='disab']",
        "select[data-automation-id*='disab']",
    ], ans["disability_workday"])

    # ── "How did you hear about us" ───────────────────────────────────────────
    await select_option(page, [
        "select[name*='source']", "select[id*='source']",
        "select[name*='referral']", "select[name*='hearAbout']",
        "select[id*='hearAbout']",
    ], ans["how_did_you_hear"])

    # ── Education ─────────────────────────────────────────────────────────────
    await select_option(page, [
        "select[name*='education']", "select[id*='education']",
        "select[name*='degree']",
    ], ans["highest_education"])

    # ── Consent checkboxes ────────────────────────────────────────────────────
    await fill_all_consent_checkboxes(page)

    # ── Custom screening questions ────────────────────────────────────────────
    try:
      await handle_screening_questions(page, job)
    except Exception as e:
      logger.debug(f"Screening question handler skipped: {e}")


# ── LinkedIn Easy Apply ────────────────────────────────────────────────────────

async def apply_linkedin(page: Page, job: dict) -> ApplyResult:
    """
    LinkedIn apply handler.
    Strategy:
    1. Try Easy Apply (LinkedIn's built-in flow)
    2. If no Easy Apply, extract the external company apply URL and route to
       the appropriate ATS handler (Workday, Greenhouse, generic, etc.)
    LinkedIn heavily blocks headless browsers, so we use a realistic
    browser profile and graceful fallbacks.
    """
    try:
        await page.goto(job["url"], timeout=45000, wait_until="domcontentloaded")
        await random_delay(3, 5)   # Extra delay — LinkedIn is bot-sensitive

        # Check if LinkedIn redirected us to login wall
        if "linkedin.com/login" in page.url or "linkedin.com/checkpoint" in page.url:
            logger.warning("LinkedIn redirected to login — cannot apply without account")
            return ApplyResult(False, "linkedin_login_required")

        # ── Try Easy Apply ─────────────────────────────────────────────────────
        easy_apply = await page.query_selector(
            "button.jobs-apply-button[aria-label*='Easy Apply'], "
            "button[aria-label*='Easy Apply']"
        )

        if easy_apply and await easy_apply.is_visible():
            logger.info(f"Easy Apply found for {job['company']}")
            await easy_apply.click()
            await random_delay(2, 3)

            for step in range(15):
                ok, reason = await handle_captcha_if_present(page)
                if not ok:
                    return ApplyResult(False, reason)

                await fill_standard_fields(page, job)

                otp_input = await page.query_selector(
                    "input[id*='otp'], input[placeholder*='verification code']"
                )
                if otp_input:
                    otp = fetch_otp_from_gmail(sender_filter="linkedin.com")
                    if otp:
                        await otp_input.fill(otp)
                        await random_delay()
                    else:
                        return ApplyResult(False, "linkedin_otp_timeout")

                submitted = await click_button(page, [
                    "button[aria-label='Submit application']",
                    "footer button[aria-label*='Submit']",
                ])
                if submitted:
                    await random_delay(2, 4)
                    shot = await save_screenshot(page, job["id"], "linkedin_easy_success")
                    return ApplyResult(True, "", shot)

                advanced = await click_button(page, [
                    "button[aria-label='Continue to next step']",
                    "footer button:has-text('Next')",
                    "footer button:has-text('Review')",
                ])
                if not advanced:
                    break
                await random_delay(1, 2)

            return ApplyResult(False, "linkedin_easy_apply_incomplete")

        # ── No Easy Apply — find external company apply URL ────────────────────
        logger.info(f"No Easy Apply on {job['company']} — checking for external apply link")

        external_url = None

        # Method 1: Find apply link directly in page HTML
        for selector in [
            "a.jobs-apply-button",
            "a[data-tracking-control-name*='apply']",
            "a[href*='apply'][class*='apply']",
            "a:has-text('Apply on company website')",
            "a:has-text('Apply on employer site')",
        ]:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    href = await el.get_attribute("href")
                    if href and href.startswith("http") and "linkedin.com" not in href:
                        external_url = href
                        logger.info(f"External URL from link: {external_url[:80]}")
                        break
            except Exception:
                continue

        # Method 2: Click apply button and catch the new tab/navigation
        if not external_url:
            try:
                async with page.context.expect_page(timeout=8000) as new_page_info:
                    await click_button(page, [
                        "button.jobs-apply-button",
                        "button:has-text('Apply')",
                        "a:has-text('Apply')",
                    ])
                new_page = await new_page_info.value
                await new_page.wait_for_load_state("domcontentloaded", timeout=10000)
                if "linkedin.com" not in new_page.url:
                    external_url = new_page.url
                    logger.info(f"External URL from new tab: {external_url[:80]}")
                    # Use the new page for applying
                    page = new_page
            except Exception:
                # No new tab — check if current page navigated
                if "linkedin.com" not in page.url:
                    external_url = page.url

        if not external_url:
            return ApplyResult(False, "linkedin_no_easy_apply")

        # Route to the right handler based on the external URL
        job_copy = {**job, "url": external_url}
        ats_type  = detect_ats(external_url)
        handler   = HANDLERS.get(ats_type)

        logger.info(f"Routing LinkedIn external URL to '{ats_type}' handler")

        if handler and handler != apply_linkedin:
            return await handler(page, job_copy)

        # Unknown ATS — use generic form handler
        return await apply_generic_form(page, job_copy)

    except Exception as e:
        return ApplyResult(False, f"linkedin_error:{e}")


# ── Greenhouse ────────────────────────────────────────────────────────────────

async def apply_greenhouse(page: Page, job: dict) -> ApplyResult:
    try:
        await page.goto(job["url"], timeout=30000)
        await random_delay()

        ok, reason = await handle_captcha_if_present(page)
        if not ok:
            return ApplyResult(False, reason)

        await fill_standard_fields(page, job)

        submitted = await click_button(page, [
            "input[type='submit']", "button#submit_app",
            "button[type='submit']",
        ])
        if submitted:
            await random_delay(2, 4)
            shot = await save_screenshot(page, job["id"], "greenhouse_success")
            return ApplyResult(True, "", shot)

        return ApplyResult(False, "greenhouse_form_incomplete")
    except Exception as e:
        return ApplyResult(False, f"greenhouse_error:{e}")


# ── Lever ─────────────────────────────────────────────────────────────────────

async def apply_lever(page: Page, job: dict) -> ApplyResult:
    try:
        await page.goto(job["url"], timeout=30000)
        await random_delay()

        ok, reason = await handle_captcha_if_present(page)
        if not ok:
            return ApplyResult(False, reason)

        await fill_standard_fields(page, job)

        submitted = await click_button(page, ["button[type='submit']"])
        if submitted:
            await random_delay(2, 4)
            shot = await save_screenshot(page, job["id"], "lever_success")
            return ApplyResult(True, "", shot)

        return ApplyResult(False, "lever_form_incomplete")
    except Exception as e:
        return ApplyResult(False, f"lever_error:{e}")


# ── Workday ───────────────────────────────────────────────────────────────────

async def apply_workday(page: Page, job: dict) -> ApplyResult:
    """
    Workday is a multi-page wizard. Navigate through all pages
    filling fields at each step until Submit is reached.
    """
    try:
        await page.goto(job["url"], timeout=30000)
        await random_delay()

        # Click the main Apply button
        clicked = await click_button(page, [
            "a[data-automation-id='adventureButton']",
            "button[data-automation-id='applyButton']",
            "a:has-text('Apply')",
        ])
        if not clicked:
            return ApplyResult(False, "workday_apply_button_not_found")

        await random_delay(2, 3)

        for step in range(20):   # Workday can have many pages
            ok, reason = await handle_captcha_if_present(page)
            if not ok:
                return ApplyResult(False, reason)

            await fill_standard_fields(page, job)

            # Check for submit
            submitted = await click_button(page, [
                "button[data-automation-id='bottom-navigation-next-button'][aria-label*='Submit']",
                "button[aria-label*='Submit']",
            ])
            if submitted:
                await random_delay(2, 4)
                shot = await save_screenshot(page, job["id"], "workday_success")
                return ApplyResult(True, "", shot)

            # Next page
            advanced = await click_button(page, [
                "button[data-automation-id='bottom-navigation-next-button']",
                "button[data-automation-id='next-button']",
                "button:has-text('Next')",
                "button:has-text('Save and Continue')",
            ])
            if not advanced:
                break
            await random_delay(2, 3)

        return ApplyResult(False, "workday_form_incomplete")
    except Exception as e:
        return ApplyResult(False, f"workday_error:{e}")


# ── Naukri ────────────────────────────────────────────────────────────────────

async def apply_naukri(page: Page, job: dict) -> ApplyResult:
    try:
        await page.goto(job["url"], timeout=30000)
        await random_delay()

        ok, reason = await handle_captcha_if_present(page)
        if not ok:
            return ApplyResult(False, reason)

        # Login if prompted
        login_btn = await page.query_selector("a#login_Layer, a.nI-gNb-lg-rg__login")
        if login_btn:
            await login_btn.click()
            await random_delay(1, 2)
            await fill_text(page, ["input#usernameField"], DEFAULT_ANSWERS["email"])
            await click_button(page, ["button[type='submit']"])
            await random_delay(2, 3)

            otp_input = await page.query_selector("input[id*='otp']")
            if otp_input:
                otp = fetch_otp_from_gmail(sender_filter="naukri.com")
                if otp:
                    await otp_input.fill(otp)
                    await click_button(page, ["button:has-text('Verify')"])
                    await random_delay()
                else:
                    return ApplyResult(False, "naukri_otp_timeout")

        await fill_standard_fields(page, job)

        clicked = await click_button(page, [
            "button#apply-button", "button.apply-button",
            "button:has-text('Apply')",
        ])
        if not clicked:
            return ApplyResult(False, "naukri_apply_button_not_found")

        await random_delay(2, 3)
        shot = await save_screenshot(page, job["id"], "naukri_success")
        return ApplyResult(True, "", shot)
    except Exception as e:
        return ApplyResult(False, f"naukri_error:{e}")


# ── Indeed ────────────────────────────────────────────────────────────────────

async def apply_indeed(page: Page, job: dict) -> ApplyResult:
    try:
        await page.goto(job["url"], timeout=30000)
        await random_delay()

        clicked = await click_button(page, [
            "button#indeedApplyButton",
            "a[data-tn-element='applyButton']",
        ])
        if not clicked:
            return ApplyResult(False, "indeed_no_apply_button")

        await random_delay(2, 3)

        for step in range(10):
            ok, reason = await handle_captcha_if_present(page)
            if not ok:
                return ApplyResult(False, reason)

            await fill_standard_fields(page, job)

            submitted = await click_button(page, [
                "button[type='submit']", "button:has-text('Submit')",
            ])
            if submitted:
                await random_delay(2, 3)
                shot = await save_screenshot(page, job["id"], "indeed_success")
                return ApplyResult(True, "", shot)

            advanced = await click_button(page, [
                "button:has-text('Continue')", "button:has-text('Next')",
            ])
            if not advanced:
                break
            await random_delay()

        return ApplyResult(False, "indeed_form_incomplete")
    except Exception as e:
        return ApplyResult(False, f"indeed_error:{e}")


# ── Dispatcher ─────────────────────────────────────────────────────────────────


# ── Generic form handler (hirist, MSD careers, Hitachi, etc.) ────────────────

async def apply_generic_form(page: Page, job: dict) -> ApplyResult:
    """
    Generic handler for company career pages and minor job boards.
    Navigates to the URL, fills all standard fields, tries to submit.
    Works for most ATS platforms we haven't built specific handlers for.
    """
    try:
        await page.goto(job["url"], timeout=30000)
        await random_delay(2, 3)

        ok, reason = await handle_captcha_if_present(page)
        if not ok:
            return ApplyResult(False, reason)

        # Some pages have an initial "Apply Now" button before the form
        await click_button(page, [
            "a:has-text('Apply Now')", "a:has-text('Apply now')",
            "button:has-text('Apply Now')", "button:has-text('Apply now')",
            "a:has-text('Apply for this job')",
            "a[class*='apply']", "button[class*='apply']",
        ])
        await random_delay(1, 2)

        # Fill all fields
        await fill_standard_fields(page, job)

        # Try submitting
        submitted = await click_button(page, [
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Submit')", "button:has-text('Submit Application')",
            "button:has-text('Apply')", "button:has-text('Send Application')",
        ])
        if submitted:
            await random_delay(2, 4)
            shot = await save_screenshot(page, job["id"], "generic_success")
            # Check for confirmation text
            content_text = await page.inner_text("body")
            success_signals = [
                "application submitted", "thank you for applying",
                "successfully applied", "application received",
                "we have received your application",
            ]
            if any(s in content_text.lower() for s in success_signals):
                logger.info(f"✓ Generic apply confirmed: {job['company']}")
                return ApplyResult(True, "", shot)
            # Submitted but no confirmation text — log as uncertain
            logger.info(f"? Generic apply submitted (unconfirmed): {job['company']}")
            return ApplyResult(True, "", shot)

        return ApplyResult(False, "generic_form_no_submit_button")

    except Exception as e:
        return ApplyResult(False, f"generic_form_error:{e}")

# ==========================================================
# ATS Detection
# ==========================================================

from urllib.parse import urlparse


def detect_ats(url: str) -> str:
    """
    Detect the ATS/provider from the application URL.
    """

    if not url:
        return "generic_form"

    host = urlparse(url.lower()).netloc
    full = url.lower()

    if "myworkdayjobs.com" in host or "workday" in host:
        return "workday"

    if "greenhouse.io" in host:
        return "greenhouse"

    if "lever.co" in host:
        return "lever"

    if "smartrecruiters.com" in host:
        return "smartrecruiters"

    if "icims.com" in host:
        return "icims"

    if "taleo.net" in host or "oraclecloud.com" in host:
        return "taleo"

    if "ashbyhq.com" in host:
        return "ashby"

    if "jobvite.com" in host:
        return "jobvite"

    if "linkedin.com" in host:
        return "linkedin"

    if "indeed.com" in host:
        return "indeed"

    if "naukri.com" in host:
        return "naukri"

    if "instahyre.com" in host:
        return "instahyre"

    if "foundit" in host or "monsterindia" in host:
        return "foundit"

    return "generic_form"

HANDLERS = {
    "workday": apply_workday,
    "greenhouse": apply_greenhouse,
    "lever": apply_lever,
    "linkedin": apply_linkedin,
    "naukri": apply_naukri,
    "indeed": apply_indeed,

    "smartrecruiters": apply_generic_form,
    "icims": apply_generic_form,
    "taleo": apply_generic_form,
    "ashby": apply_generic_form,
    "jobvite": apply_generic_form,
    "generic_form": apply_generic_form,
}


async def apply_to_job(context: BrowserContext, job: dict) -> ApplyResult:
    page = await context.new_page()
    try:
        ats     = detect_ats(job["url"])
        handler = HANDLERS.get(ats)

        if handler is None:
            return ApplyResult(False, "unknown_form_layout")

        result = await handler(page, job)
        if result.success:
            logger.info(f"✓ Applied: {job['company']} – {job['title']} ({ats})")
        else:
            logger.warning(f"✗ Failed [{job['company']} – {job['title']}]: {result.reason}")
        return result

    except Exception as e:
        logger.error(f"Unhandled apply error: {e}")
        return ApplyResult(False, f"unknown_error:{e}")
    finally:
        await page.close()


# ── Batch apply ────────────────────────────────────────────────────────────────

async def run_applications(auto_jobs: list) -> dict:
    applied, failed = [], []

    async with async_playwright() as p:

     browser = await p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )

    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        java_script_enabled=True,
        ignore_https_errors=True,
    )

    await context.add_init_script("""
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        window.chrome={runtime:{}};
        Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4]});
        Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
    """)

    return {"applied": applied, "failed": failed}
