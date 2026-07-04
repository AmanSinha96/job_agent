"""
job_filters.py — Role/location/keyword filtering criteria.

Pulled out of scraper.py so the cloud pipeline (pipeline.py -> cloud_run.py)
can import filter criteria without dragging in scraper.py's Playwright
dependency, which the jobspy-based cloud scrape no longer needs.
"""

# Search terms actually sent to jobspy as `search_term` — what gets queried.
TARGET_ROLES = [
    "Data Analyst",
    "Analytics Engineer",
    "AI Engineer",
    "Data Engineer",
    "Data Scientist",
    "Machine Learning Engineer",
    "BI Analyst",
    "AI Product Engineer",
]

# Broader title-acceptance list used by role_matches() to decide whether a
# scraped posting counts as relevant. Superset of TARGET_ROLES: a search for
# one role commonly surfaces adjacent titles worth keeping (e.g. searching
# "Data Engineer" can return a "Business Intelligence Analyst" posting).
# The MATCH_KEYWORDS/MIN_MATCH_COUNT gate below still has to pass too, so
# widening this doesn't let irrelevant postings through on its own.
ROLE_MATCH_TERMS = TARGET_ROLES + [
    "business intelligence analyst",
    "business analyst",
    "analytics manager",
    "data science",
    "ml engineer",
    "data consultant",
]

LOCATIONS = ["Bangalore", "Bengaluru", "Pune", "Gurugram", "Hyderabad"]

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
}

# Employee-count thresholds for the "big company" scoring boost in
# pipeline.compute_confidence(). jobspy reports company size as a bucketed
# string (e.g. "10,000+", "1,001 to 5,000") — parsed via pipeline.parse_company_size().
BIG_COMPANY_MIN_EMPLOYEES = 1000
MID_COMPANY_MIN_EMPLOYEES = 200
