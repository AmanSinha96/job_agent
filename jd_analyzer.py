"""
jd_analyzer.py

Extract keywords a job description shares with the candidate's actual
skill set (job_filters.MATCH_KEYWORDS). Deliberately NOT a generic
word-frequency extractor: injecting whatever words are common in a JD text
(including plain English filler on short JDs, or genuine skills the
candidate doesn't have) into the tailored Summary/Skills sections would
produce claims that don't hold up if asked about in an interview.
"""

from job_filters import MATCH_KEYWORDS


def extract_keywords(job_text, top_n=25):

    text = job_text.lower()

    matched = [kw for kw in MATCH_KEYWORDS if kw in text]

    return matched[:top_n]