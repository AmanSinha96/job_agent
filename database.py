import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# =====================================================
# Database Configuration
# =====================================================

DB_PATH = Path("data/jobs.db")

# A posting whose company+title fingerprint was already saved within this
# many days is treated as a duplicate, regardless of URL. URL-only dedup was
# letting ~44% of saved rows through as repeats (confirmed against the
# production DB, Oct 2026): LinkedIn lists one multi-city req under a
# different job ID per city ("Accenture in India — AI / ML Engineer" was
# emailed 178 times), Indeed cross-posts carry a fresh tokenized URL on every
# scrape (one Metropolis "Data Analyst" posting from March was emailed 102
# times), and the same req shows up on LinkedIn+Indeed+Workday at once.
# Bounded rather than permanent so a genuinely re-opened role months later
# can still surface.
DUPLICATE_LOOKBACK_DAYS = 30


# =====================================================
# Dedup helpers
# =====================================================

# Query params that only identify the click/session, not the job — they
# differ between scrapes of the same posting.
_TRACKING_PARAMS = {
    "src", "sid", "srcid", "xid", "xp", "px", "ref", "refid", "trackingid",
    "trk", "position", "pagenum", "from", "source", "gclid", "fbclid",
    "searchid", "jobsearchid", "originalsubdomain", "lipi", "eid",
}


def normalize_url(url):
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/",
                       urlencode(query), ""))


# Legal-entity / geography noise that differs between sources for the same
# employer: "Accenture in India" (LinkedIn) vs "Accenture" (Workday),
# "Visa Consolidated Support Services India Pvt Ltd" vs "Visa".
_COMPANY_NOISE = re.compile(
    r"\b(in india|india|pvt|private|ltd|limited|inc|llc|llp|plc|corp|corporation|"
    r"co|company|gmbh|technologies|technology|solutions|services|global|group)\b"
)
# Location/work-mode words some sources bake into the title ("Data Analyst -
# Bangalore", "AI Engineer (Remote)") — the same req, listed per city.
_TITLE_NOISE = re.compile(
    r"\b(bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|ncr|gurgaon|gurugram|"
    r"noida|kolkata|ahmedabad|india|remote|hybrid|onsite|on site|wfh|work from home|"
    r"immediate joiners?|urgent|hiring|opening)\b"
)


def _norm_words(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def job_fingerprint(company, title):
    company = _COMPANY_NOISE.sub(" ", _norm_words(company))
    title = _norm_words(title)
    title = re.sub(r"\bsr\b", "senior", title)
    title = re.sub(r"\bjr\b", "junior", title)
    title = _TITLE_NOISE.sub(" ", title)
    company = " ".join(company.split())
    title = " ".join(title.split())
    if not company or not title:
        return None
    return f"{company}|{title}"


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
        "ALTER TABLE jobs ADD COLUMN posted_at TIMESTAMP",
        "ALTER TABLE jobs ADD COLUMN fingerprint TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Backfill fingerprints for rows saved before the column existed, so
    # cross-cycle dedup covers history too, not just rows saved from now on.
    missing = conn.execute(
        "SELECT id, company, title FROM jobs WHERE fingerprint IS NULL"
    ).fetchall()
    if missing:
        conn.executemany(
            "UPDATE jobs SET fingerprint=? WHERE id=?",
            [(job_fingerprint(r["company"], r["title"]) or "", r["id"]) for r in missing],
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint, scraped_at)")

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

def load_dedup_state():
    """Return (seen_urls, recent_fingerprints) for in-memory dedup during a
    sweep — one query each instead of a DB round-trip per scraped row."""
    conn = get_connection()
    urls = {r["url"] for r in conn.execute("SELECT url FROM jobs")}
    urls |= {normalize_url(u) for u in urls}
    fingerprints = {
        r["fingerprint"] for r in conn.execute(
            "SELECT fingerprint FROM jobs WHERE fingerprint != '' "
            "AND scraped_at >= datetime('now', ?)",
            (f"-{DUPLICATE_LOOKBACK_DAYS} days",),
        )
    }
    conn.close()
    return urls, fingerprints


def save_job(job):
    """
    Save a scraped job.
    Duplicates (same normalized URL, or same company+title fingerprint
    within DUPLICATE_LOOKBACK_DAYS) are ignored.
    Returns True if inserted.
    Returns False if already exists.
    """

    url = normalize_url(job.get("url"))
    fingerprint = job_fingerprint(job.get("company"), job.get("title")) or ""

    conn = get_connection()

    existing = conn.execute(
        """
        SELECT id
        FROM jobs
        WHERE url = ?
           OR (? != '' AND fingerprint = ? AND scraped_at >= datetime('now', ?))
        """,
        (url, fingerprint, fingerprint, f"-{DUPLICATE_LOOKBACK_DAYS} days")
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
            keywords,
            posted_at,
            fingerprint
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job.get("title"),
        job.get("company"),
        job.get("location"),
        job.get("board"),
        url,
        job.get("salary"),
        job.get("confidence", 0),
        job.get("status") or "new",
        job.get("failure_reason"),
        job.get("ats", "generic"),
        job.get("tailored_resume"),
        job.get("screenshot"),
        job.get("description", ""),
        job.get("company_size", 0),
        job.get("keywords", ""),
        job.get("posted_at"),
        fingerprint,
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