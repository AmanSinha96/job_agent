# Project Context: Job Automation Agent

(Technical reference for future work on this repo — the public-facing
`README.md` is intentionally minimal.)

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
        │                    for Naukri) → rank → select top 40
        ├─ dynamic_resume_builder.build_job_specific_resume() — top 15 tailored
        ├─ build_digest_html() — HTML email body
        └─ email_sender.send_email_report() — Gmail API send, DOCX attachments
```

## Repo layout

```
cloud_run.py              — entrypoint; scrape → tailor → email → notify
pipeline.py                — sweep/rank/select, experience + salary gates
job_filters.py              — roles, cities, keyword list, blocklist, thresholds
naukri_playwright.py        — Naukri scraper (real browser; jobspy's Naukri API path is hard-blocked)
database.py                 — SQLite access (jobs table)
config.py                   — env-driven config (API keys, Gmail paths, proxies)
profile_loader.py           — candidate profile dict

dynamic_resume_builder.py   — per-job tailoring orchestrator
  jd_analyzer.py             — extracts JD keywords, restricted to real skills
  role_classifier.py         — classifies JD role type
  summary_generator.py       — Groq → Gemini → static fallback chain
  resume_parser.py           — reads sections out of resume_base.docx
  summary_editor.py          — rewrites only the Summary section
  skills_editor.py           — appends missing JD keywords into Skills
  docx_writer.py              — saves DOCX (+ PDF if LibreOffice/Word available)
  ats_validator.py            — keyword-coverage pass/fail
  resume_cache.py             — per-job-hash cache (persists within a run only)

email_sender.py             — Gmail API send, with attachment support
shared_utils.py, ats_detector.py, resume_scorer.py — small shared helpers

apply.py, applier.py        — separate, local-only tool for browser-based
                               manual-apply assistance. Not part of the cloud
                               pipeline above; run locally if you want it.
```

## Schedule

Three workflows, one per source, each with its own set of daily cron
triggers (not a fixed interval — a hand-picked schedule). Each trigger's
`--hours-old` is computed to exactly cover the gap since that workflow's own
previous run (4h floor), via a `case` on `github.event.schedule` in each
workflow's "Determine hours_old for this trigger" step — see the workflow
YAML files themselves for the exact cron/hours_old table, since this is the
kind of detail that drifts and the YAML is the source of truth.

## Current search criteria (all in `job_filters.py` unless noted)

Kept minimal intentionally for the first weeks — widen once results look right.
- Roles, cities, skill-match keyword list, blocklist: see `job_filters.py`
- `MIN_EXPERIENCE_YEARS` — rejects postings with an explicitly lower stated
  experience range; doesn't penalize postings that don't mention years
- `MIN_SALARY_LPA` (env var / GitHub secret, `config.py`) — rejects postings
  with an explicitly lower stated salary; doesn't penalize unlisted salary

## Known constraints, confirmed via live testing

- ZipRecruiter (403) and Google Jobs (0 rows, parsing issue) are not usable
  through jobspy. Not currently used.
- Naukri via jobspy's own API-based scraper is hard-blocked (406 recaptcha)
  — TLS-fingerprint spoofing and header tuning both failed to get past it.
  Naukri via a real browser (`naukri_playwright.py`) works, confirmed live
  including from an actual GitHub Actions runner, but only at ~75% success
  per request — inherent to Naukri's bot detection, handled as an expected
  per-request failure rate, not a bug to "fix".
- Naukri job descriptions come from the search-results snippet + skill tags
  + experience range, not the full JD (visiting each job's own page would
  mean more requests, more exposure to the block).
- Free-tier ceilings that can actually be hit: Groq rate limits (Gemini is
  wired as a fallback, also free-tier-limited, not infinite), GitHub Actions
  minutes (unlimited once the repo is public, 2000/month if private).
- Gmail OAuth refresh tokens expire after 7 days if the Google Cloud OAuth
  consent screen is left in "Testing" publishing status — must be "In
  production".
- `jobs.db` has no pruning — fine over weeks/months, would need a retention
  policy eventually.
- The resume-tailoring cache doesn't persist across GitHub Actions runs
  (ephemeral runner) — a job that fails to email once and retries next cycle
  re-tailors from scratch rather than hitting a cache.

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
