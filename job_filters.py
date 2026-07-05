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
    "dbt", "redshift", "postgres", "mysql", "db2",
    # Backend
    "fastapi", "rest api", "prisma",
    # Cloud & deployment
    "aws", "amplify", "rds", "vercel", "supabase", "n8n", "docker",
    "github actions", "bedrock",
    # AI / LLM
    "ai", "llm", "langchain", "openai", "claude", "gemini", "rag",
    "prompt engineering", "chromadb",
    # BI & visualization
    "tableau", "excel",
    # PDF / document processing
    "pymupdf", "pdfplumber", "camelot", "openpyxl",
    # ML foundational
    "machine learning", "scikit-learn", "sklearn", "xgboost", "lightgbm",
    "regression", "classification", "a/b testing", "statistics",
    # Integrations / product
    "graph api", "azure ad", "resend", "google analytics", "tiktok ads",
    "next.js", "typescript",
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
    "sales",
    "marketing",
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
