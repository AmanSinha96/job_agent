import sqlite3
from pathlib import Path

# =====================================================
# Database Configuration
# =====================================================

DB_PATH = Path("data/jobs.db")


def get_connection():
    """Return SQLite connection."""

    DB_PATH.parent.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    return conn


# =====================================================
# Database Initialization
# =====================================================

def init_db():
    """Create database tables."""

    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT,
            company TEXT,
            location TEXT,
            board TEXT,

            url TEXT UNIQUE,

            salary TEXT,

            confidence REAL DEFAULT 0,

            status TEXT DEFAULT 'new',

            failure_reason TEXT,

            ats TEXT,

            tailored_resume TEXT,
            screenshot TEXT,
            description TEXT,
            company_size INTEGER DEFAULT 0,
            keywords TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            applied_at TIMESTAMP

        )
    """)

    # Migrations for DBs created before these columns existed.
    for stmt in (
        "ALTER TABLE jobs ADD COLUMN company_size INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN keywords TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists

    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


# =====================================================
# Small key-value store (e.g. consecutive-zero-results streaks per site set)
# =====================================================

def get_meta(key, default=None):
    conn = get_connection()
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_meta(key, value):
    conn = get_connection()
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


# =====================================================
# Insert Job
# =====================================================

def save_job(job):
    """
    Save a scraped job.
    Duplicate URLs are ignored.
    Returns True if inserted.
    Returns False if already exists.
    """

    conn = get_connection()

    existing = conn.execute(
        """
        SELECT id
        FROM jobs
        WHERE url = ?
        """,
        (job.get("url"),)
    ).fetchone()

    if existing:
        conn.close()
        return False

    conn.execute("""
        INSERT INTO jobs (
            title,
            company,
            location,
            board,
            url,
            salary,
            confidence,
            status,
            failure_reason,
            ats,
            tailored_resume,
            screenshot,
            description,
            company_size,
            keywords
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job.get("title"),
        job.get("company"),
        job.get("location"),
        job.get("board"),
        job.get("url"),
        job.get("salary"),
        job.get("confidence", 0),
        job.get("status") or "new",
        job.get("failure_reason"),
        job.get("ats", "generic"),
        job.get("tailored_resume"),
        job.get("screenshot"),
        job.get("description", ""),
        job.get("company_size", 0),
        job.get("keywords", "")
    ))

    conn.commit()
    conn.close()

    return True


# =====================================================
# Fetch Pending Jobs
# =====================================================

def get_new_jobs(limit=100):
    """Return jobs waiting to be processed."""

    conn = get_connection()

    rows = conn.execute("""
        SELECT *
        FROM jobs
        WHERE status='new'
        ORDER BY scraped_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

    conn.close()

    return rows


# =====================================================
# Fetch Top-Ranked Jobs (post rank()/select())
# =====================================================

def get_top_jobs(limit=30):
    """Return jobs the pipeline has ranked and selected as top picks, not yet notified."""

    conn = get_connection()

    rows = conn.execute("""
        SELECT *
        FROM jobs
        WHERE status='top_pick'
        ORDER BY confidence DESC, scraped_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

    conn.close()

    return rows


# =====================================================
# Fetch All Jobs
# =====================================================

def get_all_jobs():

    conn = get_connection()

    rows = conn.execute("""
        SELECT *
        FROM jobs
        ORDER BY scraped_at DESC
    """).fetchall()

    conn.close()

    return rows


# =====================================================
# Update Status
# =====================================================

def update_status(url, status, reason=None):
    """
    Update application status.
    """

    conn = get_connection()

    conn.execute("""
        UPDATE jobs
        SET

            status=?,
            failure_reason=?,
            applied_at=CURRENT_TIMESTAMP

        WHERE url=?
    """, (

        status,
        reason,
        url

    ))

    conn.commit()
    conn.close()


# =====================================================
# Save Tailored Resume
# =====================================================

def save_resume(url, resume_path):

    conn = get_connection()

    conn.execute("""
        UPDATE jobs
        SET tailored_resume=?
        WHERE url=?
    """, (

        resume_path,
        url

    ))

    conn.commit()
    conn.close()


# =====================================================
# Save Screenshot
# =====================================================

def save_screenshot(url, screenshot_path):

    conn = get_connection()

    conn.execute("""
        UPDATE jobs
        SET screenshot=?
        WHERE url=?
    """, (

        screenshot_path,
        url

    ))

    conn.commit()
    conn.close()


# =====================================================
# Delete All Jobs
# =====================================================

def clear_jobs():

    conn = get_connection()

    conn.execute("DELETE FROM jobs")

    conn.commit()

    conn.close()


# =====================================================
# Job Count
# =====================================================

def job_count():

    conn = get_connection()

    count = conn.execute(
        "SELECT COUNT(*) FROM jobs"
    ).fetchone()[0]

    conn.close()

    return count


# =====================================================
# Testing
# =====================================================

if __name__ == "__main__":

    init_db()

    print("Database initialized successfully.")

    print(f"Total jobs: {job_count()}")