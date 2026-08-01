"""
track_outcome.py — record what happened after manually applying to a job
from the email digest (replaces the old apply.py/applier.py auto-apply
flow, dropped as unreliable — see cloud_run.py's "No auto-apply" note).

The pipeline already tracks jobs through status='new' -> 'ranked' ->
'top_pick' -> 'notified' (see pipeline.py/cloud_run.py). This repurposes
the same `status` column for what happens after notification — nothing
else in the pipeline queries for these values, so it can't collide with
rank()/select()/get_new_jobs().

Usage:
  python track_outcome.py --list                      # jobs awaiting an outcome (status='notified')
  python track_outcome.py --search "genpact"           # find a job's id across all statuses
  python track_outcome.py --id 123 --status applied
  python track_outcome.py --id 123 --status interview
  python track_outcome.py --id 123 --status rejected
  python track_outcome.py --id 123 --status offer
  python track_outcome.py --stats                      # outcome funnel across everything ever notified
"""

import argparse
import sys

import database

OUTCOME_STATUSES = ["applied", "interview", "offer", "rejected"]


def _job_row(job_id):
    conn = database.get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def print_job_table(rows):
    if not rows:
        print("No jobs found.")
        return
    print(f"\n{'ID':<6} {'Status':<11} {'Fit':>5}  {'Company':<28} {'Title':<40}")
    print("-" * 95)
    for r in rows:
        conf = f"{(r['confidence'] or 0):.0%}"
        print(f"{r['id']:<6} {r['status']:<11} {conf:>5}  {(r['company'] or '')[:27]:<28} {(r['title'] or '')[:39]:<40}")
    print()


def cmd_list(limit=30):
    conn = database.get_connection()
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='notified'").fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status='notified' ORDER BY confidence DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    print(f"\nAwaiting an outcome ({total} total, showing top {min(limit, total)} by fit):")
    print_job_table(rows)


def cmd_search(term, limit=30):
    conn = database.get_connection()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE company LIKE ? OR title LIKE ? ORDER BY scraped_at DESC LIMIT ?",
        (f"%{term}%", f"%{term}%", limit),
    ).fetchall()
    conn.close()
    print(f"\nMatches for '{term}' (showing up to {limit}, most recent first):")
    print_job_table(rows)


def cmd_set_status(job_id, status):
    job = _job_row(job_id)
    if job is None:
        print(f"No job with id {job_id}.")
        sys.exit(1)
    database.update_status(job["url"], status)
    print(f"{job['company']} — {job['title']}: marked '{status}'.")


def cmd_stats():
    conn = database.get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) as n FROM jobs "
        "WHERE status IN ('notified','applied','interview','offer','rejected') "
        "GROUP BY status"
    ).fetchall()
    conn.close()
    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())
    print(f"\nOutcome funnel ({total} jobs notified overall):")
    for status in ["notified", "applied", "interview", "offer", "rejected"]:
        print(f"  {status:<11} {counts.get(status, 0)}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Track application outcomes for notified jobs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List jobs awaiting an outcome (status='notified')")
    group.add_argument("--search", metavar="TEXT", help="Find a job's id by company/title substring")
    group.add_argument("--id", type=int, metavar="JOB_ID", help="Job's DB id (from --list/--search) to update")
    group.add_argument("--stats", action="store_true", help="Show the outcome funnel")
    parser.add_argument("--status", choices=OUTCOME_STATUSES, help="New status (required with --id)")
    parser.add_argument("--limit", type=int, default=30, help="Max rows to show for --list/--search (default 30)")
    args = parser.parse_args()

    if args.id is not None and not args.status:
        parser.error("--id requires --status")

    if args.list:
        cmd_list(args.limit)
    elif args.search:
        cmd_search(args.search, args.limit)
    elif args.id is not None:
        cmd_set_status(args.id, args.status)
    elif args.stats:
        cmd_stats()


if __name__ == "__main__":
    main()
