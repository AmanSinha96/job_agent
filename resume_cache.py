"""
resume_cache.py

Production Resume Cache

Stores and reuses tailored resumes for
identical or near-identical Job Descriptions.

Cache Structure

resume_cache/

    <job_hash>/

        resume.docx
        resume.pdf
        metadata.json
"""

import json
import shutil
from pathlib import Path


# ==========================================================
# Configuration
# ==========================================================

CACHE_ROOT = Path("resume_cache")
CACHE_ROOT.mkdir(exist_ok=True)

CACHE_VERSION = 1
BUILDER_VERSION = "5.3"


# ==========================================================
# Resume Cache
# ==========================================================

class ResumeCache:

    def __init__(

        self,

        job_hash,

    ):

        self.job_hash = str(job_hash)

        self.cache_dir = (

            CACHE_ROOT

            / self.job_hash

        )

        self.docx_file = (

            self.cache_dir

            / "resume.docx"

        )

        self.pdf_file = (

            self.cache_dir

            / "resume.pdf"

        )

        self.metadata_file = (

            self.cache_dir

            / "metadata.json"

        )

    # --------------------------------------------------

    def create(self):

        """
        Create cache folder.
        """

        self.cache_dir.mkdir(

            parents=True,

            exist_ok=True,

        )

    # --------------------------------------------------

    def exists(self):

        """
        Returns True only if every required
        cache file exists.
        """

        return (

            self.docx_file.exists()

            and

            self.pdf_file.exists()

            and

            self.metadata_file.exists()

        )

    # --------------------------------------------------

    def load_metadata(self):

        """
        Read metadata.json
        """

        if not self.metadata_file.exists():

            return {}

        with open(

            self.metadata_file,

            "r",

            encoding="utf-8",

        ) as f:

            return json.load(f)

    # --------------------------------------------------

    def save_metadata(

        self,

        metadata,

    ):

        """
        Save metadata.json
        """

        self.create()

        metadata = dict(metadata)

        metadata["cache_version"] = CACHE_VERSION

        metadata["builder_version"] = BUILDER_VERSION

        with open(

            self.metadata_file,

            "w",

            encoding="utf-8",

        ) as f:

            json.dump(

                metadata,

                f,

                indent=4,

                ensure_ascii=False,

            )

    # --------------------------------------------------

    def get_metadata(

        self,

    ):

        """
        Convenience wrapper.
        """

        return self.load_metadata()

    # --------------------------------------------------

    def save_resume(

        self,

        docx_path,

        pdf_path,

        metadata,

    ):

        """
        Save generated resume into cache.
        """

        self.create()

        shutil.copy2(

            docx_path,

            self.docx_file,

        )

        if pdf_path:

            shutil.copy2(

                pdf_path,

                self.pdf_file,

            )

        self.save_metadata(

            metadata

        )

            # --------------------------------------------------

    def validate(self):
        """
        Validate cache integrity.

        Cache is valid only if:

        • required files exist
        • metadata exists
        • cache version matches
        • builder version matches
        """

        if not self.exists():

            return False

        metadata = self.load_metadata()

        required = [

            "role",

            "company",

            "keywords",

            "resume_score",

            "keyword_match",

            "cache_version",

            "builder_version",

        ]

        for field in required:

            if field not in metadata:

                return False

        if metadata["cache_version"] != CACHE_VERSION:

            return False

        if metadata["builder_version"] != BUILDER_VERSION:

            return False

        return True

    # --------------------------------------------------

    def needs_refresh(self):
        """
        Returns True if cache should
        be regenerated.
        """

        return not self.validate()

    # --------------------------------------------------

    def load_resume(self):
        """
        Return cached resume information.
        """

        if not self.validate():

            return None

        metadata = self.load_metadata()

        return {

            "docx_path": str(

                self.docx_file

            ),

            "pdf_path": str(

                self.pdf_file

            ),

            "metadata": metadata,

        }

    # --------------------------------------------------

    def copy_to_output(

        self,

        output_docx,

        output_pdf,

    ):
        """
        Copy cached files into
        generated_resumes.
        """

        shutil.copy2(

            self.docx_file,

            output_docx,

        )

        if (

            self.pdf_file.exists()

            and output_pdf

        ):

            shutil.copy2(

                self.pdf_file,

                output_pdf,

            )

        return {

            "docx_path":

                str(output_docx),

            "pdf_path":

                str(output_pdf)

                if output_pdf

                else None,

        }

    # --------------------------------------------------

    def cache_age(self):
        """
        Last modified timestamp.
        """

        if not self.metadata_file.exists():

            return None

        return (

            self.metadata_file

            .stat()

            .st_mtime

        )

    # --------------------------------------------------

    def delete(self):
        """
        Delete this cache entry.
        """

        if self.cache_dir.exists():

            shutil.rmtree(

                self.cache_dir

            )

    # --------------------------------------------------

    def __repr__(self):

        return (

            f"<ResumeCache "

            f"{self.job_hash}>"

        )
    
    # ==========================================================
# Cache Utilities
# ==========================================================

def cache_count():
    """
    Total number of cached resumes.
    """

    return len(

        [

            d

            for d in CACHE_ROOT.iterdir()

            if d.is_dir()

        ]

    )


# ----------------------------------------------------------


def valid_cache_count():
    """
    Number of valid cache entries.
    """

    count = 0

    for folder in CACHE_ROOT.iterdir():

        if not folder.is_dir():

            continue

        cache = ResumeCache(

            folder.name

        )

        if cache.validate():

            count += 1

    return count


# ----------------------------------------------------------


def invalid_cache_count():
    """
    Number of invalid/outdated cache entries.
    """

    count = 0

    for folder in CACHE_ROOT.iterdir():

        if not folder.is_dir():

            continue

        cache = ResumeCache(

            folder.name

        )

        if not cache.validate():

            count += 1

    return count


# ----------------------------------------------------------


def cache_size():
    """
    Cache size in MB.
    """

    total = 0

    for file in CACHE_ROOT.rglob("*"):

        if file.is_file():

            total += file.stat().st_size

    return round(

        total / (1024 * 1024),

        2,

    )


# ----------------------------------------------------------


def cache_summary():
    """
    Returns cache statistics.
    """

    return {

        "entries":

            cache_count(),

        "valid":

            valid_cache_count(),

        "invalid":

            invalid_cache_count(),

        "size_mb":

            cache_size(),

    }


# ----------------------------------------------------------


def clear_cache():
    """
    Deletes the complete cache.
    """

    if CACHE_ROOT.exists():

        shutil.rmtree(

            CACHE_ROOT

        )

    CACHE_ROOT.mkdir(

        exist_ok=True

    )


# ----------------------------------------------------------


def clean_invalid_cache():
    """
    Removes only invalid/outdated cache entries.
    """

    removed = 0

    for folder in CACHE_ROOT.iterdir():

        if not folder.is_dir():

            continue

        cache = ResumeCache(

            folder.name

        )

        if not cache.validate():

            cache.delete()

            removed += 1

    return removed


# ==========================================================
# Standalone Test
# ==========================================================

if __name__ == "__main__":

    print()

    print("=" * 70)

    print("RESUME CACHE")

    print("=" * 70)

    print()

    stats = cache_summary()

    print(

        f"Cache Folder : {CACHE_ROOT}"

    )

    print(

        f"Entries      : {stats['entries']}"

    )

    print(

        f"Valid        : {stats['valid']}"

    )

    print(

        f"Invalid      : {stats['invalid']}"

    )

    print(

        f"Size (MB)    : {stats['size_mb']}"

    )

    print()

    print("=" * 70)