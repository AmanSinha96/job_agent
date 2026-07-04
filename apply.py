"""
apply.py — Manual apply trigger (runs on your laptop after reviewing email)

Usage:
  python apply.py --auto              # Apply to all pending jobs ≥80% confidence
  python apply.py --ids abc123 def456 # Apply to specific job IDs
  python apply.py --list              # Show all pending jobs without applying
  python apply.py --auto --dry-run    # Show what would be applied without submitting
"""

import argparse
import asyncio
import logging
import sqlite3
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from config import DB_PATH, AUTO_APPLY_CONFIDENCE_THRESHOLD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("apply")


def get_pending_jobs(job_ids: list | None = None) -> list[dict]:
    """Fetch jobs ready to apply from DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if job_ids:
        placeholders = ",".join("?" * len(job_ids))
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE job_id IN ({placeholders})",
            job_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('pending','pending_review') ORDER BY confidence DESC"
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_tailored_job(job: dict) -> dict:
    """Enrich job dict with tailored resume and notes from DB."""
    import json
    notes = {}
    try:
        notes = json.loads(job.get("notes") or "{}")
    except Exception:
        pass

    return {
        **job,
        "tailored": {
            "cover_letter_paragraph": notes.get("cover_para", ""),
            "key_skills_matched":     notes.get("matched", []),
            "custom_answers":         notes.get("custom_answers", {}),
        },
        "pdf_path": job.get("pdf_path"),
    }


def print_job_table(jobs: list):
    """Pretty-print job list in terminal."""
    if not jobs:
        print("No jobs found.")
        return

    print(f"\n{'ID':<8} {'Job ID':<18} {'Fit':>5}  {'Company':<28} {'Title':<35} {'Board':<10} URL Type")
    print("-" * 130)

    tier_map = {
        1: "ATS",
        2: "Career",
        3: "Board",
        4: "LinkedIn",
        99: "?",
    }

    for j in jobs:
        conf = f"{j.get('confidence', 0):.0%}"
        tier = tier_map.get(j.get("url_tier", 99), "?")

        display_job_id = (
            j.get("job_id")
            or str(j.get("id"))
            or "-"
        )

        print(
            f"{str(j.get('id','')):<8}"
            f"{display_job_id:<18}"
            f"{conf:>5}  "
            f"{str(j.get('company',''))[:27]:<28}"
            f"{str(j.get('title',''))[:34]:<35}"
            f"{str(j.get('board',''))[:9]:<10}"
            f"{tier}"
        )

    print()


async def apply_jobs(jobs: list, dry_run: bool = False):
    """Run Playwright applier on selected jobs."""
    if not jobs:
        print("No jobs to apply to.")
        return

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Applying to {len(jobs)} job(s)...\n")

    if dry_run:
        print_job_table(jobs)
        print("Dry run complete — no applications submitted.")
        return

    from applier import run_applications
    enriched = [get_tailored_job(j) for j in jobs]
    results  = await run_applications(enriched)

    applied = results["applied"]
    failed  = results["failed"]

    print(f"\n{'='*50}")
    print(f"✅ Applied:  {len(applied)}")
    print(f"❌ Failed:   {len(failed)}")

    if failed:
        print("\nFailed jobs:")
        for j in failed:
            print(f"  - {j['company']} – {j['title']}: {j.get('reason', 'unknown')}")

    print(f"{'='*50}\n")
    print("Check your email — confirmation reports sent to your report email.")


def main():
    parser = argparse.ArgumentParser(
        description="Apply to jobs reviewed from the email report"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--auto",
        action="store_true",
        help=f"Apply to all pending jobs with ≥{AUTO_APPLY_CONFIDENCE_THRESHOLD:.0%} confidence"
    )
    group.add_argument(
        "--ids",
        nargs="+",
        metavar="JOB_ID",
        help="Apply to specific job IDs (from the email report)"
    )
    group.add_argument(
        "--list",
        action="store_true",
        help="List all pending jobs without applying"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be applied without submitting"
    )
    args = parser.parse_args()

    # ── List mode ──────────────────────────────────────────────────────────────
    if args.list:
        jobs = get_pending_jobs()
        print(f"\nPending review jobs ({len(jobs)} total):")
        print_job_table(jobs)
        auto = [j for j in jobs if j.get("confidence", 0) >= AUTO_APPLY_CONFIDENCE_THRESHOLD]
        print(f"  Ready to apply (≥{AUTO_APPLY_CONFIDENCE_THRESHOLD:.0%}): {len(auto)}")
        print(f"  Review first (<{AUTO_APPLY_CONFIDENCE_THRESHOLD:.0%}):    {len(jobs) - len(auto)}")
        return

    # ── Select jobs ────────────────────────────────────────────────────────────
    if args.ids:
        jobs = get_pending_jobs(job_ids=args.ids)
        if not jobs:
            print(f"No jobs found with IDs: {args.ids}")
            sys.exit(1)
        print(f"\nSelected {len(jobs)} job(s):")
        print_job_table(jobs)
    else:  # --auto
        all_pending = get_pending_jobs()
        jobs = [j for j in all_pending if j.get("confidence", 0) >= AUTO_APPLY_CONFIDENCE_THRESHOLD]
        print(f"\nAuto mode: {len(jobs)} high-confidence jobs (≥{AUTO_APPLY_CONFIDENCE_THRESHOLD:.0%}) selected from {len(all_pending)} pending:")
        print_job_table(jobs)

    if not jobs:
        print("Nothing to apply to.")
        return

    # ── Confirm before applying ────────────────────────────────────────────────
    if not args.dry_run:
        confirm = input(f"Apply to {len(jobs)} job(s)? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    asyncio.run(apply_jobs(jobs, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
