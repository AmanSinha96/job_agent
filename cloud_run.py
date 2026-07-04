"""
cloud_run.py — Cloud pipeline entrypoint (run by GitHub Actions)

Scrape -> filter -> tailor resume per qualifying job -> email digest
with tailored resumes attached for manual apply.

No auto-apply: the applicant reviews the email and applies manually
using the attached, JD-tailored resume.
"""

import argparse
import asyncio
import logging
import traceback
from datetime import datetime, timezone

import pipeline
import database
from profile_loader import load_profile
from dynamic_resume_builder import build_job_specific_resume
from email_sender import send_email_report
from config import GMAIL_CREDENTIALS_FILE, GMAIL_TOKEN_FILE, GMAIL_SCOPES, NOTIFY_EMAIL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cloud_run")

TAILOR_TOP_N = 15


def tailor_jobs(jobs: list[dict], profile: dict) -> tuple[list[dict], list[dict]]:
    """Tailor a resume for the best-ranked jobs, up to TAILOR_TOP_N.

    `jobs` arrives pre-sorted best-match-first (database.get_top_jobs() orders
    by confidence DESC). If tailoring fails for a top-ranked job it falls
    through to `other_matches` and the next-best job gets tried instead, so
    we still end up with TAILOR_TOP_N tailored resumes whenever possible.
    """
    tailored, other_matches = [], []
    for job in jobs:
        if len(tailored) >= TAILOR_TOP_N:
            other_matches.append(job)
            continue
        try:
            result = build_job_specific_resume(profile, job)
        except Exception as e:
            logger.warning("Resume tailoring failed for %s @ %s: %s", job.get("title"), job.get("company"), e)
            other_matches.append({**job, "reason": str(e)})
            continue
        tailored.append({**job, **result})
    return tailored, other_matches


def build_digest_html(tailored: list[dict], other_matches: list[dict], total_found: int, cycle_start: datetime) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %I:%M %p UTC")
    start_str = cycle_start.strftime("%Y-%m-%d %I:%M %p UTC")

    def matched_keywords(j, limit=6):
        # DB stores this as a comma-string; build_job_specific_resume()'s
        # result dict (merged into tailored jobs) also has a "keywords" key,
        # but as a list — handle both.
        kw = j.get("keywords") or []
        if isinstance(kw, str):
            kw = [k for k in kw.split(",") if k.strip()]
        return ", ".join(kw[:limit]) or "—"

    def salary_display(j):
        return j.get("salary") or "—"

    def tailored_rows():
        if not tailored:
            return "<tr><td colspan='6' style='color:#888'>None this cycle</td></tr>"
        rows = ""
        for j in tailored:
            rows += (
                f"<tr>"
                f"<td><a href='{j.get('url','')}' style='color:#2e7d32'>{j.get('title','')}</a></td>"
                f"<td>{j.get('company','')}</td>"
                f"<td>{j.get('location','')}</td>"
                f"<td>{salary_display(j)}</td>"
                f"<td>{j.get('board','')}</td>"
                f"<td>{j.get('resume_score', 0)}% — {matched_keywords(j)}</td>"
                f"</tr>"
            )
        return rows

    def other_rows():
        if not other_matches:
            return "<tr><td colspan='6' style='color:#888'>None this cycle</td></tr>"
        rows = ""
        for j in other_matches:
            match_pct = f"{round(j.get('confidence', 0) * 100)}%" if j.get("confidence") is not None else "—"
            rows += (
                f"<tr>"
                f"<td><a href='{j.get('url','')}' style='color:#e65100'>{j.get('title','')}</a></td>"
                f"<td>{j.get('company','')}</td>"
                f"<td>{j.get('location','')}</td>"
                f"<td>{salary_display(j)}</td>"
                f"<td>{j.get('board','')}</td>"
                f"<td>{match_pct} — {matched_keywords(j)}</td>"
                f"</tr>"
            )
        return rows

    table_style = "width:100%;border-collapse:collapse;font-family:sans-serif;font-size:14px"
    th_style = "background:#f5f5f5;padding:8px;text-align:left;border-bottom:2px solid #ddd;color:#333"

    return f"""
    <html><body style="font-family:sans-serif;color:#222;max-width:700px;margin:auto">
    <h2 style="color:#1a73e8">Job Agent Report — {now}</h2>
    <p style="color:#555">Cycle: {start_str} → {now}</p>

    <table style="width:100%;margin-bottom:20px">
      <tr>
        <td style="background:#e8f5e9;padding:16px;border-radius:8px;text-align:center">
          <div style="font-size:32px;font-weight:bold;color:#2e7d32">{len(tailored)}</div>
          <div style="color:#555">Tailored & Ready to Apply</div>
        </td>
        <td style="width:12px"></td>
        <td style="background:#fff3e0;padding:16px;border-radius:8px;text-align:center">
          <div style="font-size:32px;font-weight:bold;color:#e65100">{len(other_matches)}</div>
          <div style="color:#555">Other Strong Matches</div>
        </td>
      </tr>
    </table>

    <h3 style="color:#2e7d32">✅ Tailored & Ready to Apply ({len(tailored)})</h3>
    <p style="color:#555;font-size:13px">Tailored resume for each is attached — download and apply directly.</p>
    <table style="{table_style}">
      <tr><th style="{th_style}">Role</th><th style="{th_style}">Company</th>
          <th style="{th_style}">Location</th><th style="{th_style}">Salary</th>
          <th style="{th_style}">Source</th><th style="{th_style}">Match / Why</th></tr>
      {tailored_rows()}
    </table>

    <h3 style="color:#e65100;margin-top:24px">⚡ Other Strong Matches — Not Tailored ({len(other_matches)})</h3>
    <p style="color:#555;font-size:13px">Still a strong match, just outside the top {TAILOR_TOP_N} we build tailored resumes for — apply with your base resume, or ask for one of these to be tailored too.</p>
    <table style="{table_style}">
      <tr><th style="{th_style}">Role</th><th style="{th_style}">Company</th>
          <th style="{th_style}">Location</th><th style="{th_style}">Salary</th>
          <th style="{th_style}">Source</th><th style="{th_style}">Match / Why</th></tr>
      {other_rows()}
    </table>

    <hr style="margin-top:32px;border:none;border-top:1px solid #eee">
    <table style="width:100%;font-size:13px;color:#555">
      <tr><td><b>📊 Stats</b></td></tr>
      <tr><td>New jobs scraped this cycle: {total_found}</td></tr>
      <tr><td>Tailored & ready: {len(tailored)}</td></tr>
      <tr><td>Other strong matches: {len(other_matches)}</td></tr>
    </table>
    </body></html>
    """


ZERO_RESULT_ALERT_THRESHOLD = 3
ZERO_RESULT_REMINDER_EVERY = 6


def notify_zero_results(sites: list[str], streak: int):
    """A source silently returning nothing for several cycles in a row
    usually means it's IP-blocked/rate-limited, not that postings genuinely
    dried up — and since nothing raises an exception, this would otherwise
    go unnoticed indefinitely."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = f"""
    <html><body style="font-family:sans-serif;color:#222;max-width:700px;margin:auto">
    <h2 style="color:#e65100">👀 No new jobs from {', '.join(sites)} for {streak} cycles in a row</h2>
    <p style="color:#555">As of {now}. Usually means the source is blocked/rate-limited rather than
    postings genuinely drying up — see README's "Known limitations" for which sources are fragile.</p>
    </body></html>
    """
    try:
        sent = send_email_report(
            subject=f"👀 Job Agent: {', '.join(sites)} found nothing for {streak} cycles",
            html_body=html,
            notify_email=NOTIFY_EMAIL,
            credentials_file=GMAIL_CREDENTIALS_FILE,
            token_file=GMAIL_TOKEN_FILE,
            scopes=GMAIL_SCOPES,
        )
        logger.info("Zero-result notification sent." if sent else "Zero-result notification send returned False.")
    except Exception as e:
        logger.error("Could not send zero-result streak notification: %s", e)


def check_zero_result_streak(sites: list[str], saved: int):
    key = f"zero_result_streak:{','.join(sorted(sites))}"
    if saved > 0:
        database.set_meta(key, 0)
        return
    streak = int(database.get_meta(key, 0) or 0) + 1
    database.set_meta(key, streak)
    if streak == ZERO_RESULT_ALERT_THRESHOLD or (
        streak > ZERO_RESULT_ALERT_THRESHOLD and streak % ZERO_RESULT_REMINDER_EVERY == 0
    ):
        notify_zero_results(sites, streak)


def main(sites: list[str], hours_old: int):
    cycle_start = datetime.now(timezone.utc)
    database.init_db()

    logger.info("Sweep: sites=%s hours_old=%s", sites, hours_old)
    saved = asyncio.run(pipeline.run(sites, hours_old))
    logger.info("Sweep complete: %d new jobs saved", saved)
    check_zero_result_streak(sites, saved)

    jobs = [dict(row) for row in database.get_top_jobs()]
    logger.info("%d top-ranked jobs to tailor", len(jobs))

    if not jobs:
        logger.info("Nothing to tailor or email this cycle.")
        return

    profile = load_profile()
    tailored, other_matches = tailor_jobs(jobs, profile)
    logger.info("Tailoring done: %d tailored, %d other matches", len(tailored), len(other_matches))

    html = build_digest_html(tailored, other_matches, saved, cycle_start)
    # DOCX only — no LibreOffice/Word in this environment for PDF conversion,
    # and DOCX parses at least as reliably in most ATS anyway.
    attachments = [j["docx_path"] for j in tailored if j.get("docx_path")]

    sent = send_email_report(
        subject=f"Job Agent Report — {cycle_start.strftime('%Y-%m-%d')}",
        html_body=html,
        notify_email=NOTIFY_EMAIL,
        credentials_file=GMAIL_CREDENTIALS_FILE,
        token_file=GMAIL_TOKEN_FILE,
        scopes=GMAIL_SCOPES,
        attachments=attachments,
    )
    logger.info("Email sent: %s", sent)

    if sent:
        for job in tailored + other_matches:
            database.update_status(job["url"], "notified")
    else:
        logger.warning(
            "Email send failed — leaving %d jobs as 'top_pick' so they're retried next cycle "
            "instead of being silently dropped.", len(tailored) + len(other_matches)
        )


def notify_failure(sites: list[str], hours_old: int, exc: Exception):
    """Best-effort failure alert — a broken pipeline should tell you, not go
    silent for a week. If the email send itself fails too, this just logs —
    the GitHub Actions run still shows red in the Actions tab either way."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = f"""
    <html><body style="font-family:sans-serif;color:#222;max-width:700px;margin:auto">
    <h2 style="color:#c62828">⚠️ Job Agent cycle FAILED — {now}</h2>
    <p style="color:#555">sites={sites}, hours_old={hours_old}</p>
    <pre style="background:#f5f5f5;padding:12px;border-radius:6px;overflow-x:auto;
                font-size:12px;white-space:pre-wrap">{tb}</pre>
    </body></html>
    """
    try:
        sent = send_email_report(
            subject=f"⚠️ Job Agent FAILED — {now}",
            html_body=html,
            notify_email=NOTIFY_EMAIL,
            credentials_file=GMAIL_CREDENTIALS_FILE,
            token_file=GMAIL_TOKEN_FILE,
            scopes=GMAIL_SCOPES,
        )
        logger.info("Failure notification sent." if sent else "Failure notification send returned False.")
    except Exception as e:
        logger.error("Could not even send the failure notification email: %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # zip_recruiter (403 forbidden) and naukri (406 recaptcha required) are
    # currently hard-blocked at the network level, and google returns zero
    # rows even for generic high-volume queries (looks like a parsing issue
    # in jobspy's Google implementation, not a block) — confirmed via live
    # testing, reproducible on retry. Only indeed and linkedin are verified
    # working right now. Re-add sites here once/if they start returning
    # results again — no other code changes needed.
    parser.add_argument("--sites", default="indeed",
                         help="Comma-separated jobspy site_name values")
    parser.add_argument("--hours-old", type=int, default=4)
    args = parser.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()]

    try:
        main(sites=sites, hours_old=args.hours_old)
    except Exception as e:
        logger.exception("Cycle crashed: %s", e)
        notify_failure(sites, args.hours_old, e)
        raise  # keep the GitHub Actions run marked failed
