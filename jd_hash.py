"""
jd_hash.py

Generates a stable hash for a Job Description.

Purpose
-------
Avoid regenerating the same tailored resume for
nearly identical jobs.

The hash is generated from:

- Role
- Company (optional)
- Normalized JD
- Important keywords

Two nearly identical JDs will produce the same hash.
"""

import hashlib
import re


# ---------------------------------------------------------
# Text Normalization
# ---------------------------------------------------------

def normalize_text(text: str) -> str:
    """
    Normalize text before hashing.

    Removes:
    - extra spaces
    - punctuation
    - line breaks

    Converts everything to lowercase.
    """

    if not text:
        return ""

    text = text.lower()

    text = text.replace("\n", " ")

    text = text.replace("\r", " ")

    text = re.sub(r"\s+", " ", text)

    text = re.sub(r"[^a-z0-9+#.\- ]", "", text)

    return text.strip()


# ---------------------------------------------------------
# Keyword Normalization
# ---------------------------------------------------------

def normalize_keywords(keywords):
    """
    Removes duplicates and sorts keywords
    so ordering doesn't affect the hash.
    """

    if not keywords:
        return []

    cleaned = []

    for keyword in keywords:

        keyword = normalize_text(keyword)

        if keyword:

            cleaned.append(keyword)

    return sorted(list(set(cleaned)))


# ---------------------------------------------------------
# Build Hash Source
# ---------------------------------------------------------

def build_hash_source(

    role,

    description,

    keywords,

    company=None,

):
    """
    Build deterministic string used for hashing.
    """

    parts = []

    parts.append(

        normalize_text(role)

    )

    if company:

        parts.append(

            normalize_text(company)

        )

    parts.append(

        normalize_text(description)

    )

    parts.extend(

        normalize_keywords(

            keywords

        )

    )

    return "|".join(parts)


# ---------------------------------------------------------
# SHA256 Hash
# ---------------------------------------------------------

def generate_job_hash(

    role,

    description,

    keywords,

    company=None,

    length=16,

):
    """
    Generate deterministic hash.

    Returns

    Example

    a84e10e9c73a4d91
    """

    source = build_hash_source(

        role,

        description,

        keywords,

        company,

    )

    digest = hashlib.sha256(

        source.encode("utf-8")

    ).hexdigest()

    return digest[:length]


# ---------------------------------------------------------
# Compare Two Jobs
# ---------------------------------------------------------

def same_job(

    job1,

    job2,

):
    """
    Compare two job dictionaries.

    Returns True if both jobs would
    generate the same tailored resume.
    """

    hash1 = generate_job_hash(

        role=job1.get(

            "role",

            job1.get(

                "title",

                "",

            ),

        ),

        company=job1.get(

            "company",

            "",

        ),

        description=job1.get(

            "description",

            "",

        ),

        keywords=job1.get(

            "keywords",

            [],

        ),

    )

    hash2 = generate_job_hash(

        role=job2.get(

            "role",

            job2.get(

                "title",

                "",

            ),

        ),

        company=job2.get(

            "company",

            "",

        ),

        description=job2.get(

            "description",

            "",

        ),

        keywords=job2.get(

            "keywords",

            [],

        ),

    )

    return hash1 == hash2


# ---------------------------------------------------------
# Cache Folder Name
# ---------------------------------------------------------

def cache_folder(hash_value):
    """
    Returns cache folder name.

    Example

    resume_cache/a84e10e9c73a4d91
    """

    return f"resume_cache/{hash_value}"


# ---------------------------------------------------------
# Metadata Builder
# ---------------------------------------------------------

def build_metadata(

    role,

    company,

    keywords,

    resume_score,

    keyword_match,

):
    """
    Metadata saved alongside
    cached resumes.
    """

    return {

        "role": role,

        "company": company,

        "keywords": keywords,

        "resume_score": resume_score,

        "keyword_match": keyword_match,

    }


# ---------------------------------------------------------
# Standalone Test
# ---------------------------------------------------------

if __name__ == "__main__":

    jd = """
    Looking for a Data Engineer with
    Python, SQL, Airflow,
    Snowflake and AWS experience.
    """

    keywords = [

        "Python",

        "SQL",

        "Airflow",

        "Snowflake",

        "AWS",

    ]

    job_hash = generate_job_hash(

        role="Data Engineer",

        company="Google",

        description=jd,

        keywords=keywords,

    )

    print()

    print("=" * 60)

    print("JOB HASH")

    print("=" * 60)

    print()

    print(job_hash)

    print()

    print(cache_folder(job_hash))

    print()

    print("=" * 60)