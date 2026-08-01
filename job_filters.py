"""
job_filters.py — Role/location/keyword filtering criteria.

Pulled out of scraper.py so the cloud pipeline (pipeline.py -> cloud_run.py)
can import filter criteria without dragging in scraper.py's Playwright
dependency, which the jobspy-based cloud scrape no longer needs.
"""

# Search terms actually sent to jobspy as `search_term` — what gets queried.
# Trimmed 2026-07: Data Engineer/Data Scientist/Machine Learning Engineer
# removed per explicit request — keeping the role list minimal for the
# first week before widening again.
TARGET_ROLES = [
    "Data Analyst",
    "Analytics Engineer",
    "AI Engineer",
    "BI Analyst",
    "AI Product Engineer",
]

# Title-acceptance rule used by pipeline.role_matches(): a posting is kept
# if its title contains ALL words in at least one group below, in ANY order
# and not necessarily adjacent. Word-based rather than literal-phrase
# matching on purpose — real postings phrase the same role differently
# ("Senior Analyst, Analytics" vs "Analytics Analyst", "AI Platform
# Engineer" vs "AI Engineer") and a fixed-phrase list misses these even
# though they're the same role, not a different one. Still gated by the
# MATCH_KEYWORDS/MIN_MATCH_COUNT floor below, so this doesn't admit
# irrelevant roles just from title words alone — deliberately NOT including
# a bare {"software", "engineer"} group for that reason, that's too generic
# on title alone regardless of keyword gating.
ROLE_WORD_GROUPS = [
    {"data", "analyst"},
    {"analytics", "analyst"},
    {"analytics", "engineer"},
    {"ai", "engineer"},
    {"ai", "product", "engineer"},
    {"bi", "analyst"},
    {"bi", "engineer"},
    {"business intelligence", "analyst"},
    {"business intelligence", "engineer"},
    {"business", "analyst"},
    {"analytics", "manager"},
    {"data", "consultant"},
    {"tableau", "analyst"},
]

# Titles containing one of these exact phrases were explicitly excluded
# from TARGET_ROLES above (Data Engineer/Data Scientist/Machine Learning
# Engineer, dropped 2026-07 per explicit request to keep the role list
# narrow) — but ROLE_WORD_GROUPS' word-based (non-adjacent) matching could
# still readmit them via an unrelated group: "Data Engineer II, Data &
# Analytics" matches {"analytics","engineer"} even though the actual role
# IS a Data Engineer role with "Analytics" just naming the team, not
# describing a different, wanted role. Confirmed live: 156 "Data Engineer",
# 16 "Data Scientist", and 20 "Machine Learning Engineer" titled postings
# were kept this way, including the single highest-confidence job in the
# whole database. Reject titles containing one of these phrases outright
# UNLESS they also contain a redeemer word signaling a genuinely hybrid/
# adjacent role, not a plain excluded one.
EXCLUDED_ROLE_PHRASES = ["data engineer", "data scientist", "machine learning engineer"]
EXCLUDED_ROLE_REDEEMERS = {"analyst", "bi", "product"}

# Trimmed 2026-07: Bengaluru dropped (same city as Bangalore, was double-
# counting search combos), Gurugram dropped for now. Pune added back.
LOCATIONS = ["Bangalore", "Hyderabad", "Pune"]

# Aligned to Aman's actual resume (resume_base.docx) — real stack only, so
# the confidence score reflects genuine fit rather than generic buzzwords.
# Kept: python/sql/aws/fastapi/docker/github actions/n8n/langchain/openai/
# claude/gemini/rag/prompt engineering/supabase/vercel/amplify/redshift/dbt.
# Dropped: spark, airflow, bigquery, power bi, looker(+studio), metabase,
# superset, agentic — not on the resume at all.
MATCH_KEYWORDS = [
    # Core / data engineering
    "python", "sql", "data", "analytics", "etl", "elt", "pipeline",
    "dbt", "redshift", "postgres", "postgresql", "mysql", "db2",
    # Backend
    "fastapi", "rest api", "restful", "prisma", "cron",
    # Cloud & deployment
    "aws", "amplify", "rds", "vercel", "supabase", "n8n", "docker",
    "github actions", "bedrock",
    # AI / LLM — langchain removed 2026-08: not actually on the resume
    # (audit found it inflating scores on LangChain-heavy postings with no
    # real matching experience), human-in-the-loop added (it is, twice).
    "ai", "llm", "openai", "claude", "gemini", "rag",
    "prompt engineering", "chromadb", "human-in-the-loop",
    # BI & visualization
    "tableau", "excel",
    # PDF / document processing
    "pymupdf", "pdfplumber", "camelot", "openpyxl",
    # ML foundational
    "machine learning", "scikit-learn", "sklearn", "xgboost", "lightgbm",
    "regression", "classification", "a/b testing", "ab testing",
    "a/b tests", "ab tests", "statistics",
    # Integrations / product
    "graph api", "azure ad", "resend", "google analytics", "tiktok ads",
    "next.js", "nextjs", "typescript",
]

MIN_MATCH_COUNT = 2

BLOCKED_KEYWORDS = {
    "intern",
    "internship",
    "frontend",
    "react",
    "php",
    "wordpress",
    "laravel",
    "ios",
    "android",
    "mobile developer",
    "customer support",
    "call center",
    "designer",
    "graphic",
    "account executive",
    "medical",
    "teacher",
    "content writer",
    # Junior/entry-level exclusion — candidate is 6+ years experienced,
    # explicitly does not want junior/entry postings recommended.
    "entry level",
    "entry-level",
    "fresher",
    "freshers",
    "trainee",
    "campus hire",
    "graduate program",
    "junior",
}

# "marketing"/"sales" scanned against the FULL JD text (like the rest of
# BLOCKED_KEYWORDS above) caused false rejections — the candidate's actual
# specialty is marketing analytics (google analytics/tiktok ads are real
# MATCH_KEYWORDS entries), so any Data/BI Analyst JD that merely mentions
# "supports the marketing org" or "partners with sales" in passing got
# hard-rejected for an unrelated reason. These two are only a meaningful
# "wrong job family" signal when they describe the ROLE ITSELF, so they're
# checked against the title only, not the whole description — see
# pipeline.should_keep().
TITLE_ONLY_BLOCKED_KEYWORDS = {
    "sales",
    "marketing",
}

# Minimum years of experience a posting must require to be kept — a JD
# stating a lower experience range (e.g. "0-3 years") gets rejected even if
# it doesn't use an obvious junior/entry keyword above. See
# pipeline.extract_min_experience_years().
MIN_EXPERIENCE_YEARS = 5

# Employee-count thresholds + bonus points for the "big company" scoring
# boost in pipeline.compute_confidence(). jobspy reports company size as a
# bucketed string (e.g. "10,000+", "1,001 to 5,000") — parsed via
# pipeline.parse_company_size(). Raised 2026-07 from 15/7 to 25/12 — company
# size was previously a minor tiebreaker; now a genuinely significant
# ranking factor so large/well-paying companies surface preferentially,
# not just get a nudge over an equally-matched small company.
BIG_COMPANY_MIN_EMPLOYEES = 1000
BIG_COMPANY_BONUS = 25
MID_COMPANY_MIN_EMPLOYEES = 200
MID_COMPANY_BONUS = 12
