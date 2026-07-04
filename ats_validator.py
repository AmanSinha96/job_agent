"""
ats_validator.py
"""

MIN_ATS_SCORE = 65


def validate_resume(
    resume_text,
    keywords,
):
    """
    Phase 4 compatible validator
    """

    resume_lower = resume_text.lower()

    missing = []

    for kw in keywords:

        if kw.lower() not in resume_lower:

            missing.append(kw)

    matched = (
        len(keywords)
        - len(missing)
    )

    score = 0

    if keywords:

        score = round(
            matched
            / len(keywords)
            * 100,
            2
        )

    return {
        "pass": score >= MIN_ATS_SCORE,
        "score": score,
        "missing_keywords": missing,
    }