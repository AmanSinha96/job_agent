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
from shared_utils import keyword_matches


def extract_keywords(job_text, top_n=25):
    # Was `kw in text` — plain substring containment with no word
    # boundaries at all, so "postgres" matched inside "postgresql" and
    # every other false-positive class already fixed in shared_utils.py's
    # matching (e.g. "sales" inside "salesforce") was silently still live
    # here, in a completely separate implementation that fix never
    # touched. Reuses the same word-boundary-aware matcher used for job
    # scoring so both paths agree on what actually counts as a match.
    return keyword_matches(job_text, MATCH_KEYWORDS)[:top_n]