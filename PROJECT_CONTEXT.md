# Project Context: Job Automation Agent

## Overview
Scrapes fresh job postings for a fixed set of roles/cities, ranks them by
real JD/keyword match plus a company-size boost, tailors a resume (Summary +
Skills sections only) for the best matches via an LLM, and emails a digest
with the tailored DOCX resumes attached. No auto-apply — every application
is manual, using the attached tailored resume.

## Architecture

```
GitHub Actions cron (2 workflows, different cadence per source)
        │
        ▼
cloud_run.py — entrypoint
        │
        ├─ pipeline.run() — sweep (jobspy) → rank → select top 30
        ├─ dynamic_resume_builder.build_job_specific_resume() — top 15 tailored
        ├─ build_digest_html() — HTML email body
        └─ email_sender.send_email_report() — Gmail API send, DOCX attachments
```

Two GitHub Actions workflows because LinkedIn needs a slower cadence than
Indeed to avoid IP-block risk on the shared runner pool:
- `job_sweep.yml` — every 4h, `--sites indeed`
- `job_sweep_linkedin.yml` — every 12h (offset 2h to avoid a git-push race
  with the other workflow's "commit updated data" step)

## Known constraints, confirmed via live testing

- ZipRecruiter (403), Naukri (406 recaptcha), and Google Jobs (0 rows,
  parsing issue) are not currently usable through jobspy. Only Indeed and
  LinkedIn are live. See README's "Known limitations".
- Free-tier ceilings that can actually be hit: Groq rate limits (Gemini is
  wired as a fallback, also free-tier-limited, not infinite), GitHub Actions
  minutes (unlimited once the repo is public, 2000/month if private).
- Gmail OAuth refresh tokens expire after 7 days if the Google Cloud OAuth
  consent screen is left in "Testing" publishing status — must be "In
  production".

## Local-only, separate feature

`apply.py`/`applier.py` is a Playwright-based browser automation tool for
manual-apply assistance, run locally on your own machine. It is entirely
separate from the cloud pipeline above — `cloud_run.py` does not import it.

## History note

This repo previously had a much larger, mostly-broken legacy layer: a
Playwright-based scraper (`scraper.py` + `scrapers/*.py`), a local
`schedule`-loop orchestrator (`main.py`), and several partially-implemented
auto-apply/resume-variant modules, most of which called database functions
(`get_conn`, `initialize_database`) that no longer exist after `database.py`
was refactored. All of that was removed — the modules listed under
Architecture above are the entire live pipeline.
