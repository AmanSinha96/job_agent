# Project Context: Job Automation Agent

## Overview
Scrapes fresh job postings for a fixed set of roles/cities, ranks them by
real JD/keyword match plus a company-size boost, tailors a resume (Summary +
Skills sections only) for the best matches via an LLM, and emails a digest
with the tailored DOCX resumes attached. No auto-apply — every application
is manual, using the attached tailored resume.

## Architecture

```
GitHub Actions cron (3 workflows, different cadence per source)
        │
        ▼
cloud_run.py — entrypoint
        │
        ├─ pipeline.run() — sweep (jobspy for Indeed/LinkedIn, Playwright
        │                    for Naukri) → rank → select top 30
        ├─ dynamic_resume_builder.build_job_specific_resume() — top 15 tailored
        ├─ build_digest_html() — HTML email body
        └─ email_sender.send_email_report() — Gmail API send, DOCX attachments
```

Three GitHub Actions workflows, one per source, because each has a different
IP-block/rate-limit risk profile on the shared runner pool:
- `job_sweep.yml` — every 4h, `--sites indeed`
- `job_sweep_naukri.yml` — every 6h, `--sites naukri` (Playwright-based, see below)
- `job_sweep_linkedin.yml` — every 12h

All three are cron-offset (0/4/8/12/16/20, 1/7/13/19, 2/14 UTC respectively)
so their "commit updated data" steps never race each other.

## Known constraints, confirmed via live testing

- ZipRecruiter (403) and Google Jobs (0 rows, parsing issue) are not usable
  through jobspy. Not currently used.
- Naukri via jobspy's own API-based scraper is hard-blocked (406 recaptcha)
  — TLS-fingerprint spoofing and header tuning both failed to get past it.
  Naukri via a real browser (`naukri_playwright.py`) works, confirmed live
  including from an actual GitHub Actions runner, but only at ~75% success
  per request — inherent to Naukri's bot detection, handled as an expected
  per-request failure rate, not a bug to "fix". See README's "Known limitations".
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
