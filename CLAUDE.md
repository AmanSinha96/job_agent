# CLAUDE.md — Job Automation Agent

## What this repo is

A GitHub-Actions-scheduled pipeline: scrape job postings (jobspy) → filter/
rank → tailor a resume's Summary+Skills sections per top job (Groq → Gemini
→ static fallback) → email a digest with tailored DOCX resumes attached. No
auto-apply — the user applies manually. See `README.md` for the full
architecture and `PROJECT_CONTEXT.md` for current known constraints.

## Entry points

- `cloud_run.py` — the only thing GitHub Actions runs. Also runnable
  directly as a local script (`python cloud_run.py --sites indeed --hours-old 24`).
- `apply.py`/`applier.py` — separate, local-only browser-automation tool.
  Not part of the cloud pipeline; don't assume it's wired into `cloud_run.py`.

## Rules specific to this repo

- `pipeline.py`, `dynamic_resume_builder.py`, and everything under the
  `dynamic_resume_builder.py` import closure (see README's Repo layout) is
  the *only* actively used code. Before assuming a module is dead, check
  whether `cloud_run.py` reaches it transitively — this repo previously had
  a much larger legacy layer (removed) where several modules called
  database functions that no longer exist.
- `job_filters.py`'s `MATCH_KEYWORDS`/`TARGET_ROLES`/`ROLE_MATCH_TERMS` are
  hand-curated to match the actual candidate resume (`resume_base.docx`) —
  don't add generic buzzwords back in without checking they're genuinely on
  the resume. `jd_analyzer.extract_keywords()` deliberately only surfaces
  JD terms that intersect this list, specifically so tailoring never injects
  a skill the candidate doesn't actually have.
- `skills_editor.py`'s paragraph-writing logic depends on this resume's
  specific DOCX structure (section titles are plain "Normal"-styled
  all-caps text, not real Word Heading styles; skills lines are two runs —
  bold "Category: " label + normal values). If you change how sections/
  headings are detected, re-run a full before/after paragraph diff against
  `resume_base.docx` before trusting it — this exact class of bug
  previously blanked the entire Work Experience/Certifications/Education
  sections silently.
- Never widen the site list in the GitHub Actions workflows back to
  zip_recruiter/naukri/google without re-testing live first — they were
  removed after confirmed (not assumed) failures. See README's "Known
  limitations".
- Secrets (`GMAIL_CREDENTIALS`, `GMAIL_TOKEN_B64`, `GROQ_API_KEY`,
  `GEMINI_API_KEY`) are GitHub Actions secrets only. Never commit
  `credentials.json`/`token.json`/`.env` — already covered by `.gitignore`.

## Testing changes

There's no test suite. Verify changes by actually running the affected path:
- Scraping/filtering changes: `python -c "import asyncio, pipeline; print(asyncio.run(pipeline.run(['indeed'], 72, roles=[...], locations=[...])))"` against a scratch `data/jobs.db`.
- Resume-tailoring changes: run `dynamic_resume_builder.build_job_specific_resume()` against `resume_base.docx` with a synthetic job dict, then diff paragraph text/`pPr` before vs. after against the original file — don't just eyeball the target section, confirm nothing else moved.
