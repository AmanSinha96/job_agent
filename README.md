# Job Automation Agent

Scrapes fresh job postings for a configured set of roles/cities, ranks them by
JD/keyword match and company size, tailors a resume (Summary + Skills only,
rest of the resume untouched) for the best matches, and emails a digest with
the tailored resume attached — every 4 hours for Indeed, every 6 hours for
Naukri, every 12 hours for LinkedIn.

**No auto-apply.** You review the email and apply manually with the attached,
JD-tailored resume.

## Stack (all free except LLM tailoring, which is cheap/free-tier)

| Layer | Tool |
|---|---|
| Scraping (Indeed/LinkedIn) | [python-jobspy](https://github.com/speedyapply/JobSpy) — HTTP-based, no browser (see Known limitations) |
| Scraping (Naukri) | Playwright + stealth, real browser — jobspy's own Naukri scraper hits a hard 406 every time, see `naukri_playwright.py` |
| Ranking | Keyword match against your real skill set + company-size boost |
| Resume tailoring | Groq (free tier) → Gemini (free tier) fallback → static template last resort |
| Resume output | DOCX only (no LibreOffice/Word in CI for PDF conversion — DOCX also parses at least as reliably in most ATS) |
| Email | Gmail API (OAuth2) |
| Storage | SQLite, committed back to the repo each cycle |
| Scheduling | GitHub Actions cron (three workflows, see below) |

## How it works

```
Every 4h (Indeed) / 6h (Naukri) / 12h (LinkedIn):
│
├── 1. SWEEP    — jobspy (Indeed/LinkedIn) or Playwright (Naukri) scrapes
│                 each (role × city) combo, hours_old-bounded
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

naukri_playwright.py        — Naukri scraper (real browser, jobspy's Naukri API path is hard-blocked)

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

Three workflows in `.github/workflows/`, cron-offset so their "commit updated
data" steps never race each other:
- **job_sweep.yml** — every 4h (`0,4,8,12,16,20` UTC), `--sites indeed`
- **job_sweep_naukri.yml** — every 6h (`1,7,13,19` UTC), `--sites naukri` —
  needs Playwright + Chromium installed, slower per-request than the HTTP-based
  sources
- **job_sweep_linkedin.yml** — every 12h (`2,14` UTC), `--sites linkedin`

There's also `test_naukri_playwright.yml` — a manual-only (`workflow_dispatch`)
diagnostic, not scheduled, that checks whether Naukri scraping still works
before you rely on it. Naukri's block is inherently flaky (~75% success rate
observed, not 100% — see Known limitations) — re-run this if the Naukri
workflow's job counts look off.

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

- **ZipRecruiter**: 403 forbidden — network-level block. Not currently used.
- **Google Jobs**: returns zero rows even for generic, high-volume queries —
  looks like a parsing issue in jobspy's current Google implementation, not
  a block. Not currently used.
- **Naukri via jobspy's own API-based scraper: hard-blocked**, 406
  "recaptcha required" on every request — confirmed with TLS-fingerprint
  spoofing and header tuning, neither helped. **Naukri via a real browser
  (`naukri_playwright.py`) works, but not reliably**: confirmed live from an
  actual GitHub Actions runner at ~75% success per request (3/4 in repeated
  tests), with an intermittent 403 on the rest. This is inherent to Naukri's
  bot detection, not a bug in this code — every Naukri request is wrapped so
  a blocked one is skipped like any other empty search result, never crashes
  the run. Don't expect Naukri's job count to be as consistent as Indeed's.
- Re-adding ZipRecruiter/Google is a one-line change (`--sites` arg in the
  workflow) if jobspy fixes them, or if you add a paid rotating proxy
  (`PROXIES` secret, wired but off by default) — no free option reliably
  solves network-level blocking like ZipRecruiter's.
- `jobs.db` has no pruning — fine over a month, would need a retention policy
  over a year+.
- The resume-tailoring cache doesn't persist across GitHub Actions runs
  (ephemeral runner) — a job that fails to email once and retries next cycle
  re-tailors from scratch rather than hitting a cache.
- Naukri job descriptions come from the search-results snippet + skill tags,
  not the full JD (which would need visiting each job's own page — more
  requests, more exposure to the block). Good enough for keyword filtering
  and tailoring; shallower than Indeed's full descriptions.
