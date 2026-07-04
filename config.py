from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

# ============================================================
# Base Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)

SCREENSHOT_DIR = BASE_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

GENERATED_RESUME_DIR = BASE_DIR / "generated_resumes"
GENERATED_RESUME_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "jobs.db"

MASTER_RESUME = BASE_DIR / "resume_base.docx"

# ============================================================
# APIs
# ============================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

# Fallback LLM when Groq is rate-limited/unavailable — free tier on ai.google.dev
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

# Optional rotating-proxy pool for jobspy (comma-separated proxy URLs).
# Free per-run IP rotation already happens via GitHub Actions' ephemeral
# runners; this is only for rotating IPs *within* a single run. Empty by
# default — no free reliable option exists for that, needs a paid provider.
PROXIES = [p.strip() for p in os.getenv("PROXIES", "").split(",") if p.strip()]

NOPECHA_API_KEY = os.getenv(
    "NOPECHA_API_KEY",
    ""
)

# Optional if later used
OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    ""
)

# ============================================================
# Candidate Profile
# ============================================================

FIRST_NAME = os.getenv("FIRST_NAME", "")
LAST_NAME = os.getenv("LAST_NAME", "")

EMAIL = os.getenv("JOB_EMAIL", "")
PHONE = os.getenv("PHONE", "")

NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", EMAIL)

LINKEDIN_URL = os.getenv("LINKEDIN_URL", "")
GITHUB_URL = os.getenv("GITHUB_URL", "")

YEARS_EXP = os.getenv("YEARS_EXP", "6")
SALARY_LPA = os.getenv("SALARY_LPA", "25")

MIN_SALARY_LPA = float(
    os.getenv("MIN_SALARY_LPA", "23")
)

EXPERIENCE_LEVEL = os.getenv(
    "EXPERIENCE_LEVEL",
    "Senior"
)

DEFAULT_ANSWERS = {
    "first_name": FIRST_NAME,
    "last_name": LAST_NAME,
    "full_name": f"{FIRST_NAME} {LAST_NAME}",
    "email": EMAIL,
    "phone": PHONE,
    "linkedin_url": LINKEDIN_URL,
    "github_url": GITHUB_URL,
    "years_experience": YEARS_EXP,
    "salary_display": f"{SALARY_LPA} LPA",
    "expected_salary_lpa": SALARY_LPA,
    "current_salary_lpa": SALARY_LPA,
    "notice_period": "30 days",
    "work_authorized": "Yes",
    "requires_sponsorship": "No",
}

# ============================================================
# Auto Apply Settings
# ============================================================

AUTO_APPLY_CONFIDENCE_THRESHOLD = float(
    os.getenv("AUTO_APPLY_CONFIDENCE_THRESHOLD", "0.75")
)

MAX_APPLICATIONS_PER_RUN = int(
    os.getenv("MAX_APPLICATIONS_PER_RUN", "20")
)

HEADLESS = os.getenv(
    "HEADLESS",
    "False"
).lower() == "true"

PAGE_LOAD_TIMEOUT = int(
    os.getenv("PAGE_LOAD_TIMEOUT", "30000")
)

# ============================================================
# Browser
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

# ============================================================
# Logging
# ============================================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO"
)

# ============================================================
# Resume Settings
# ============================================================

RESUME_CACHE_ENABLED = True

RESUME_OUTPUT_FORMAT = "docx"

# ============================================================
# Misc
# ============================================================

ENABLE_SCREENSHOTS = True

SAVE_FAILED_APPLICATIONS = True

RETRY_FAILED_APPLICATIONS = False

# ============================================================
# Search Settings
# ============================================================

SEARCH_TERM = os.getenv("SEARCH_TERM", "Data Engineer")

TARGET_ROLES = [
    "Data Analyst",
    "Analytics Engineer",
    "AI Engineer",
    "Data Engineer"
]

LOCATIONS = ["Bangalore", "Remote", "Hyderabad"]

PORTALS = ["linkedin", "indeed", "naukri"]


# ============================================================
# ATS Mapping
# ============================================================

ATS_HANDLERS = {
    "myworkdayjobs.com": "workday",
    "workday.com": "workday",
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "ashbyhq.com": "ashby",
    "smartrecruiters.com": "smartrecruiters",
    "icims.com": "icims",
    "jobvite.com": "jobvite",
    "oraclecloud.com": "taleo",
    "taleo.net": "taleo",
    "linkedin.com": "linkedin",
    "indeed.com": "indeed",
    "naukri.com": "naukri",
    "foundit.in": "foundit",
    "instahyre.com": "instahyre",
}

# ============================================================
# Human Delays
# ============================================================

MIN_DELAY_SECONDS = float(
    os.getenv("MIN_DELAY_SECONDS", "0.8")
)

MAX_DELAY_SECONDS = float(
    os.getenv("MAX_DELAY_SECONDS", "2.0")
)

MIN_APPLY_DELAY = float(
    os.getenv("MIN_APPLY_DELAY", "10")
)

MAX_APPLY_DELAY = float(
    os.getenv("MAX_APPLY_DELAY", "25")
)

# ============================================================
# Gmail OTP
# ============================================================

GMAIL_CREDENTIALS_FILE = BASE_DIR / "credentials.json"

GMAIL_TOKEN_FILE = BASE_DIR / "token.json"

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify"
]

# ============================================================
# Candidate defaults used by applier.py
# ============================================================

DEFAULT_ANSWERS.update({

    "current_location": os.getenv("CURRENT_LOCATION", ""),

    "current_city": os.getenv("CURRENT_CITY", ""),

    "current_state": os.getenv("CURRENT_STATE", ""),

    "current_country": os.getenv("CURRENT_COUNTRY", "India"),

    "current_pincode": os.getenv("CURRENT_PINCODE", ""),

    "address_line1": os.getenv("ADDRESS_LINE1", ""),

    "address_line2": os.getenv("ADDRESS_LINE2", ""),

    "gender": os.getenv("GENDER", "Prefer not to say"),

    "highest_education": os.getenv(
        "HIGHEST_EDUCATION",
        "Bachelor's Degree"
    ),

    "how_did_you_hear": "LinkedIn",

    "veteran_status_workday": "I am not a protected veteran",

    "disability_workday": "I don't wish to answer",

    "generic_why_company":
        "I am excited about the opportunity to contribute to {company} as a {job_title}. My experience in data engineering and analytics aligns well with the role.",

    "generic_experience_answer":
        "I have over six years of professional experience in data engineering, analytics, cloud platforms, ETL pipelines and Python development."

})
