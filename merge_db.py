"""
merge_db.py — merge this run's data/jobs.db into a newer copy from main.

Used by scripts/commit_data.sh when the push of the updated jobs.db is
rejected because another workflow pushed first. jobs.db is a binary SQLite
file, so git can only pick one whole side — and the old workflow step's
`git rebase -X ours` picked the WRONG side: during a rebase "ours" is the
upstream branch being rebased onto, so this run's newly saved/notified jobs
were silently discarded every time two runs overlapped, and the next run
re-saved and re-emailed them.

Merge rules:
  - jobs: rows missing from the target are inserted; for rows present in
    both (same url), the more advanced status wins
    (new < ranked < top_pick < notified < applied/interview/offer/rejected).
  - meta: three-way against --base (the jobs.db this run started from) —
    a key this run changed takes this run's value; otherwise the target's
    (newer) value is kept, so this run doesn't roll back another workflow's
    streak counters / last-sweep timestamps.

Usage: python merge_db.py --ours OURS.db --base BASE.db --into data/jobs.db
"""
import argparse
import sqlite3
from pathlib import Path

import database

STATUS_RANK = {"new": 0, "ranked": 1, "top_pick": 2, "notified": 3,
               "applied": 4, "interview": 4, "offer": 4, "rejected": 4}


def _ensure_schema(path):
    original = database.DB_PATH
    database.DB_PATH = Path(path)
    try:
        database.init_db()
    finally:
        database.DB_PATH = original


def _read_meta(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT key, value FROM meta")}


def merge(ours_path, base_path, into_path):
    for path in (ours_path, into_path):
        _ensure_schema(path)

    ours = sqlite3.connect(ours_path)
    ours.row_factory = sqlite3.Row
    into = sqlite3.connect(into_path)
    into.row_factory = sqlite3.Row

    cols = [r[1] for r in into.execute("PRAGMA table_info(jobs)") if r[1] != "id"]
    target = {r["url"]: (r["id"], r["status"]) for r in into.execute("SELECT id, url, status FROM jobs")}

    inserted = upgraded = 0
    for row in ours.execute(f"SELECT {', '.join(cols)} FROM jobs"):
        existing = target.get(row["url"])
        if existing is None:
            into.execute(
                f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(row[c] for c in cols),
            )
            inserted += 1
        elif STATUS_RANK.get(row["status"], 0) > STATUS_RANK.get(existing[1], 0):
            into.execute(
                "UPDATE jobs SET status=?, failure_reason=?, applied_at=? WHERE id=?",
                (row["status"], row["failure_reason"], row["applied_at"], existing[0]),
            )
            upgraded += 1

    base_meta = {}
    if base_path and Path(base_path).exists() and Path(base_path).stat().st_size:
        try:
            base = sqlite3.connect(base_path)
            base_meta = _read_meta(base)
            base.close()
        except sqlite3.DatabaseError:
            base_meta = {}
    meta_changed = 0
    for key, value in _read_meta(ours).items():
        if base_meta.get(key) != value:
            into.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            meta_changed += 1

    into.commit()
    into.close()
    ours.close()
    print(f"merge_db: inserted {inserted} jobs, advanced status on {upgraded}, "
          f"carried over {meta_changed} changed meta keys")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ours", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--into", default=str(database.DB_PATH))
    args = parser.parse_args()
    merge(args.ours, args.base, args.into)
