"""
summary_generator.py

Production ATS Summary Generator
"""

import re
import logging

from groq import Groq

from config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
)

logger = logging.getLogger(__name__)

client = None
gemini_client = None

if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)

if GEMINI_API_KEY:
    from google import genai
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def _call_groq(prompt: str) -> str:
    if client is None:
        raise RuntimeError("Groq unavailable — no GROQ_API_KEY")
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.2,
        max_tokens=220,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def _call_gemini(prompt: str) -> str:
    if gemini_client is None:
        raise RuntimeError("Gemini unavailable — no GEMINI_API_KEY")
    response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text.strip()


# Common preambles/wrapping an LLM sometimes adds despite being told
# "return only the summary" — stripped so they never end up verbatim in a
# resume a recruiter reads.
_LLM_PREAMBLE_RE_LIST = [
    r'^(?:sure[,!]?\s+)?here(?:\'s| is)\s+(?:the\s+)?(?:rewritten\s+|revised\s+|updated\s+|tailored\s+)?(?:professional\s+)?summary\s*:?\s*',
    r'^sure[,!]?\s*:?\s*',
]


def _clean_llm_output(text: str) -> str:
    text = text.strip().strip('"').strip("'").strip()
    for pattern in _LLM_PREAMBLE_RE_LIST:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    return text


# Proper display casing for keywords that are acronyms/proper nouns — used
# only in the static fallback sentence below, so a technology name doesn't
# read as "Aws"/"Sql" if naively title-cased.
_DISPLAY_CASE = {
    "sql": "SQL", "aws": "AWS", "ai": "AI", "llm": "LLM", "rag": "RAG",
    "dbt": "DBT", "etl": "ETL", "elt": "ELT", "rds": "RDS", "bi": "BI",
    "db2": "DB2", "rest api": "REST API", "restful": "RESTful",
    "next.js": "Next.js", "nextjs": "NextJS", "a/b testing": "A/B testing",
    "ab testing": "A/B testing", "a/b tests": "A/B tests", "ab tests": "A/B tests",
}


def _display_keyword(kw: str) -> str:
    return _DISPLAY_CASE.get(kw, kw[:1].upper() + kw[1:] if kw else kw)


def generate_summary(
    profile,
    job,
    keywords=None,
):

    keywords = keywords or []

    description = ""

    if isinstance(job, dict):
        description = job.get(
            "description",
            ""
        )
    else:
        description = str(job)

    current_summary = profile.get(
        "summary",
        ""
    )

    skills = profile.get(
        "skills",
        ""
    )

    experience = profile.get(
        "experience",
        ""
    )

    keyword_text = ", ".join(
        keywords[:20]
    )

    prompt = f"""
You are an expert resume writer.

Your task is NOT to invent new experience.

Rewrite ONLY the Professional Summary.

Current Summary

{current_summary}

Candidate Experience

{experience}

Technical Skills

{skills}

Target Job Description

{description}

Important ATS Keywords

{keyword_text}

Rules

1. 90-120 words.
2. Professional tone.
3. ATS optimized.
4. Mention only technologies already present in the candidate profile.
5. Naturally include the important keywords.
6. Do NOT exaggerate.
7. Do NOT mention years of experience unless already known.
8. Return only the summary.
"""

    # Returns (summary_text, source) — source lets callers (cloud_run.py)
    # surface which path actually served each job as a GitHub Actions
    # annotation, since a silent Groq/Gemini failure here previously had no
    # visible signal outside of logger.warning (invisible without log
    # access, which 403s without admin/write auth on this repo).
    try:
        return _clean_llm_output(_call_groq(prompt)), "groq"
    except Exception as e:
        logger.warning("Groq summary generation failed, falling back to Gemini: %s", e)

    try:
        return _clean_llm_output(_call_gemini(prompt)), "gemini"
    except Exception as e:
        logger.warning("Gemini summary generation failed, using static fallback: %s", e)

    return _static_fallback_summary(keywords), "fallback"


def _static_fallback_summary(keywords) -> str:
    # Only reached when BOTH Groq and Gemini fail. The previous version
    # returned byte-identical prose across every JD except for one raw,
    # lowercase, comma-separated keyword clause bolted onto the end (e.g.
    # "...with expertise in python, sql, analytics, rds, ai, tableau, a/b
    # testing.") — an obvious "auto-generated" tell to any recruiter
    # comparing two of the candidate's applications, and ungrammatical
    # next to the properly capitalized prose around it. Weaves 2-3 real
    # keywords into an actual sentence instead, with correct display
    # casing for acronyms (via _display_keyword) rather than a bare dump.
    top = [_display_keyword(k) for k in keywords[:3]]
    if len(top) >= 2:
        keyword_clause = ", ".join(top[:-1]) + f", and {top[-1]}"
    elif top:
        keyword_clause = top[0]
    else:
        keyword_clause = None

    summary = (
        "AI Product Engineer and Analytics professional experienced in "
        "building production-grade AI applications, scalable data pipelines, "
        "and cloud-based analytics solutions. Skilled in Python, SQL, AWS, "
        "FastAPI, DBT, Tableau, and modern LLM technologies while delivering "
        "end-to-end products from client requirements through deployment. "
        "Strong experience collaborating with stakeholders to develop "
        "automation, reporting, and decision-support solutions."
    )
    if keyword_clause:
        summary += f" Hands-on experience with {keyword_clause} in particular."
    return summary