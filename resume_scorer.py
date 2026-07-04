"""
resume_scorer.py
"""


def score_resume(
    keyword_match,
    ats_result,
):
    """
    Produce final resume score.
    """

    ats_score = ats_result.get(
        "score",
        0
    )

    final_score = (
        keyword_match * 0.6
        + ats_score * 0.4
    )

    return round(
        final_score,
        2
    )