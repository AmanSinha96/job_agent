# Job Automation Agent

Scrapes fresh job postings for a configured set of roles/cities, ranks them by
JD/keyword match and company size, tailors a resume (Summary + Skills only,
rest of the resume untouched) for the best matches, and emails a digest with
the tailored resume attached — every 4 hours for Indeed, every 12 hours for
LinkedIn.

**No auto-apply.** You review the email and apply manually with the attached,
JD-tailored resume.

## Stack (all free except LLM tailoring, which is cheap/free-tier)

| Layer | Tool |
|---|---|
| Scraping | [python-jobspy](https://github.com/speedyapply/JobSpy) — Indeed + LinkedIn (see Known limitations) |
| Ranking | Keyword match against your real skill set + company-size boost |
| Resume tailoring | Groq (free tier) → Gemini (free tier) fallback → static template last resort |
| Resume output | DOCX only (no LibreOffice/Word in CI for PDF conversion — DOCX also parses at least as reliably in most ATS) |
| Email | Gmail API (OAuth2) |
| Storage | SQLite, committed back to the repo each cycle |
| Scheduling | GitHub Actions cron (two workflows, see below) |

## How it works

```
Every 4h (Indeed) / 12h (LinkedIn):
│
├── 1. SWEEP    — jobspy scrapes each (role × city) combo, hours_old-bounded
├── 2. FILTER   — role/location/keyword match, blocklist, dedup by URL
├── 3. RANK     — confidence = keyword match + ATS-detected bonus + company-size boost
├── 4. SELECT   — top 30 by confidence become "top_pick"
├── 5. TAILOR   — top 15 of those get a JD-tailored resume (Summary + Skills
│                 only — Experience/Projects/Education untouched); the rest
│                 show up as links only ("Other Strong Matches")
├── 6. EMAIL    — HTML digest, tailored DOCX resumes attached
└── 7. NOTIFY   — jobs marked 'notified' only if the email actually sent —
                  otherwise retried next cycle, never silently dropped
```

Any uncaught failure anywhere in the cycle triggers a separate "Job Agent
FAILED" email with the traceback, so a broken pipeline tells you instead of
going silent (`cloud_run.py`'s `notify_failure()`).

## Repo layout

```
cloud_run.py              — entrypoint; scrape → tailor → email → notify
pipeline.py                — jobspy sweep/rank/select
job_filters.py              — roles, cities, keyword list (aligned to your resume), blocklist
database.py                 — SQLite access (jobs table)
config.py                   — env-driven config (API keys, Gmail paths, proxies)
profile_loader.py           — candidate profile dict

dynamic_resume_builder.py   — per-job tailoring orchestrator
  jd_analyzer.py             — extracts JD keywords, restricted to your real skills
  role_classifier.py         — classifies JD role type
  summary_generator.py       — Groq → Gemini → static fallback chain
  resume_parser.py           — reads sections out of resume_base.docx
  summary_editor.py          — rewrites only the Summary section
  skills_editor.py           — appends missing JD keywords into Skills, one line per category
  docx_writer.py              — saves DOCX (+ PDF if LibreOffice/Word available)
  ats_validator.py            — keyword-coverage pass/fail
  resume_cache.py             — per-job-hash cache (persists within a run only)

email_sender.py             — Gmail API send, with attachment support
shared_utils.py, ats_detector.py, resume_scorer.py — small shared helpers

apply.py, applier.py        — separate, local-only tool for browser-based
                               manual-apply assistance. Not part of the cloud
                               pipeline above; run locally if you want it.
```

## GitHub Actions setup

Two workflows in `.github/workflows/`:
- **job_sweep.yml** — every 4h, `--sites indeed`
- **job_sweep_linkedin.yml** — every 12h (offset 2h from the frequent job so
  their "commit updated data" steps never race each other), `--sites linkedin`

Required repository secrets:
```
GROQ_API_KEY        — groq.com, free tier
GEMINI_API_KEY       — ai.google.dev, free tier (optional but recommended fallback)
GMAIL_CREDENTIALS    — full contents of your Gmail API OAuth credentials.json
GMAIL_TOKEN_B64      — base64 of a token.json with a valid refresh_token
JOB_EMAIL            — your contact email, shown in the tailored resume/profile
NOTIFY_EMAIL         — where the digest gets sent
FIRST_NAME, LAST_NAME, PHONE, LINKEDIN_URL, GITHUB_URL,
YEARS_EXP, SALARY_LPA, MIN_SALARY_LPA, EXPERIENCE_LEVEL — candidate profile fields
PROXIES              — optional, comma-separated proxy URLs for jobspy (unused by default)
```

Your Google Cloud OAuth consent screen needs to be in **"In production"**
publishing status, not "Testing" — Testing-mode refresh tokens auto-expire
after 7 days regardless of use, which silently kills email delivery.

## Known limitations (confirmed via live testing, not guesses)

- **ZipRecruiter**: 403 forbidden — network-level block.
- **Naukri**: 406 "recaptcha required" — CAPTCHA wall on first request.
- **Google Jobs**: returns zero rows even for generic, high-volume queries —
  looks like a parsing issue in jobspy's current Google implementation, not
  a block.
- **Indeed and LinkedIn are the only confirmed-working sources right now.**
  Re-adding a site is a one-line change (`--sites` arg in the workflow) if/
  when jobspy fixes these or you add a paid rotating proxy (`PROXIES` secret,
  wired but off by default — no free option reliably solves network-level
  blocking like ZipRecruiter's/Naukri's).
- `jobs.db` has no pruning — fine over a month, would need a retention policy
  over a year+.
- The resume-tailoring cache doesn't persist across GitHub Actions runs
  (ephemeral runner) — a job that fails to email once and retries next cycle
  re-tailors from scratch rather than hitting a cache.
