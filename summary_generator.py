"""
summary_generator.py

Production ATS Summary Generator
"""

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

    try:
        return _call_groq(prompt)
    except Exception as e:
        logger.warning("Groq summary generation failed, falling back to Gemini: %s", e)

    try:
        return _call_gemini(prompt)
    except Exception as e:
        logger.warning("Gemini summary generation failed, using static fallback: %s", e)

    top_keywords = ", ".join(keywords[:8])

    return (
            "AI Product Engineer and Analytics professional experienced in "
            "building production-grade AI applications, scalable data pipelines, "
            "and cloud-based analytics solutions. Skilled in Python, SQL, AWS, "
            "FastAPI, DBT, Tableau, and modern LLM technologies while delivering "
            "end-to-end products from client requirements through deployment. "
            "Strong experience collaborating with stakeholders to develop "
            "automation, reporting, and decision-support solutions with expertise "
            f"in {top_keywords}."
        )