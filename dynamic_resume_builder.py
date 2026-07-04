"""
dynamic_resume_builder.py

Production Resume Builder

Pipeline

Master Resume (DOCX)
        │
        ▼
Parse Resume
        │
        ▼
Extract JD Keywords
        │
        ▼
Classify Role
        │
        ▼
Generate Summary
        │
        ▼
Update Summary
        │
        ▼
Update Skills
        │
        ▼
Update Experience
        │
        ▼
Update Projects
        │
        ▼
Save DOCX
        │
        ▼
Generate PDF
        │
        ▼
ATS Validation
        │
        ▼
Resume Score
        │
        ▼
Resume Cache
"""

from pathlib import Path

from docx import Document

from jd_analyzer import extract_keywords
from role_classifier import classify_role
from summary_generator import generate_summary

from resume_parser import ResumeParser

from summary_editor import SummaryEditor
from skills_editor import SkillsEditor

from docx_writer import ResumeWriter

from ats_validator import validate_resume
from resume_scorer import score_resume

from jd_hash import generate_job_hash
from resume_cache import ResumeCache


# ==========================================================
# Configuration
# ==========================================================

MASTER_RESUME = Path(

    "resume_base.docx"

)

OUTPUT_DIR = Path(

    "generated_resumes"

)

OUTPUT_DIR.mkdir(

    exist_ok=True

)


# ==========================================================
# Helpers
# ==========================================================

def load_master_resume():

    """
    Load master DOCX.
    """

    if not MASTER_RESUME.exists():

        raise FileNotFoundError(

            f"{MASTER_RESUME} not found."

        )

    return Document(

        str(MASTER_RESUME)

    )


# ----------------------------------------------------------


def rebuild_resume_text(

    document,

):
    """
    Convert edited DOCX back to plain text.

    Used for ATS validation.
    """

    lines = []

    for paragraph in document.paragraphs:

        text = paragraph.text.strip()

        if text:

            lines.append(text)

    return "\n".join(lines)


# ----------------------------------------------------------


def build_profile(

    parser,

):
    """
    Build profile dictionary from the
    latest parsed resume.
    """

    return {

        "summary":

            parser.get_summary(),

        "skills":

            parser.get_skills(),

        "experience":

            parser.get_experience(),

        "projects":

            parser.get_projects(),

    }


# ----------------------------------------------------------


def keyword_match_score(

    resume_text,

    keywords,

):
    """
    Calculate keyword match percentage.
    """

    if not keywords:

        return 0

    resume_lower = resume_text.lower()

    matched = 0

    for keyword in keywords:

        if keyword.lower() in resume_lower:

            matched += 1

    return round(

        matched

        / len(keywords)

        * 100,

        2,

    )

# ==========================================================
# Main Resume Builder
# ==========================================================

def build_job_specific_resume(

    profile,

    job,

):
    """
    Build a tailored resume for one job.
    """

    # ------------------------------------------------------
    # Job Information
    # ------------------------------------------------------

    company = job.get(

        "company",

        "Unknown",

    )

    role = (

        job.get(

            "title",

            ""

        )

        or

        job.get(

            "role",

            ""

        )

    )

    description = job.get(

        "description",

        ""

    )

    if not description.strip():

        raise ValueError(

            "Job description is empty."

        )

    # ------------------------------------------------------
    # Analyze JD
    # ------------------------------------------------------

    keywords = extract_keywords(

        description

    )

    role_type = classify_role(

        description

    )

    # ------------------------------------------------------
    # Generate Cache Hash
    # ------------------------------------------------------

    job_hash = generate_job_hash(

        role=role,

        company=company,

        description=description,

        keywords=keywords,

    )

    cache = ResumeCache(

        job_hash

    )

    # ------------------------------------------------------
    # Resume Writer
    # ------------------------------------------------------

    writer = ResumeWriter(

        None

    )

    output_filename = writer.build_filename(

        candidate=profile.get(

            "name",

            "Candidate",

        ),

        role=role,

        company=company,

    )

    output_docx = (

        OUTPUT_DIR

        / f"{output_filename}.docx"

    )

    output_pdf = (

        OUTPUT_DIR

        / f"{output_filename}.pdf"

    )

    # ------------------------------------------------------
    # Use Cache
    # ------------------------------------------------------

    if (

        cache.exists()

        and

        cache.validate()

    ):

        copied = cache.copy_to_output(

            output_docx,

            output_pdf,

        )

        metadata = cache.get_metadata()

        return {

            "docx_path":

                copied["docx_path"],

            "pdf_path":

                copied["pdf_path"],

            "resume_score":

                metadata["resume_score"],

            "keyword_match":

                metadata["keyword_match"],

            "ats_pass":

                metadata["ats_pass"],

            "keywords":

                metadata["keywords"],

            "cached":

                True,

        }

    # ------------------------------------------------------
    # Load Master Resume
    # ------------------------------------------------------

    document = load_master_resume()

    parser = ResumeParser(

        document

    )

    parser.build_sections()

    # ------------------------------------------------------
    # Refresh Profile
    # ------------------------------------------------------

    profile = build_profile(

        parser

    )

    # ------------------------------------------------------
    # Generate Summary
    # ------------------------------------------------------

    summary = generate_summary(

        profile,

        job,

        keywords,

    )

    SummaryEditor(

        document

    ).update(

        summary

    )

        # ------------------------------------------------------
    # Update Skills
    # ------------------------------------------------------

    parser = ResumeParser(

        document

    )

    parser.build_sections()

    SkillsEditor(

        parser,

        keywords,

    ).update()

    # Experience and Projects are intentionally left untouched — only the
    # Summary and Skills sections get tailored per job, keeping the rest of
    # the resume's structure and content exactly as written.

    # ------------------------------------------------------
    # Save Resume
    # ------------------------------------------------------

    writer = ResumeWriter(

        document

    )

    output = writer.save(

        candidate=profile.get(

            "name",

            "Candidate",

        ),

        role=role,

        company=company,

    )

    # ------------------------------------------------------
    # Rebuild Resume Text
    # ------------------------------------------------------

    parser = ResumeParser(

        document

    )

    parser.build_sections()

    resume_text = parser.get_resume_text()

    # ------------------------------------------------------
    # ATS Validation
    # ------------------------------------------------------

    ats_result = validate_resume(

        resume_text,

        keywords,

    )

    keyword_match = keyword_match_score(

        resume_text,

        keywords,

    )

    resume_score = score_resume(

        keyword_match,

        ats_result,

    )

    # ------------------------------------------------------
    # Save Cache
    # ------------------------------------------------------

    metadata = {

        "company": company,

        "role": role,

        "keywords": keywords,

        "resume_score": resume_score,

        "keyword_match": keyword_match,

        "ats_pass": ats_result["pass"],

    }

    cache.save_resume(

        docx_path=output["docx_path"],

        pdf_path=output["pdf_path"],

        metadata=metadata,

    )

    # ------------------------------------------------------
    # Return
    # ------------------------------------------------------

    return {

        "docx_path":

            output["docx_path"],

        "pdf_path":

            output["pdf_path"],

        "resume_score":

            resume_score,

        "keyword_match":

            keyword_match,

        "ats_pass":

            ats_result["pass"],

        "keywords":

            keywords,

        "role_type":

            role_type,

        "cached":

            False,

    }