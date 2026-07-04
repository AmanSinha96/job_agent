# First-time setup

## 1. Gmail API credentials

1. [Google Cloud Console](https://console.cloud.google.com) → new project → enable **Gmail API**.
2. **OAuth consent screen** → set publishing status to **"In production"**, not
   "Testing". Testing-mode refresh tokens auto-expire after 7 days regardless
   of use — this silently kills email delivery a week in.
3. **Credentials** → **Create OAuth client ID** → Desktop app → download the
   JSON. This is your `GMAIL_CREDENTIALS` secret content, verbatim.
4. Generate a `token.json` once, locally:
   ```bash
   python -c "
   from google_auth_oauthlib.flow import InstalledAppFlow
   flow = InstalledAppFlow.from_client_secrets_file('credentials.json', ['https://www.googleapis.com/auth/gmail.send'])
   creds = flow.run_local_server(port=0)
   open('token.json', 'w').write(creds.to_json())
   "
   ```
   This opens a browser once for you to authorize. Then base64-encode it:
   ```bash
   base64 -w0 token.json   # macOS: base64 -i token.json
   ```
   That output is your `GMAIL_TOKEN_B64` secret.

## 2. LLM keys

- `GROQ_API_KEY` — [console.groq.com](https://console.groq.com), free tier, no billing required.
- `GEMINI_API_KEY` — [ai.google.dev](https://ai.google.dev), free tier, no billing required. Used only as a fallback when Groq is rate-limited.

## 3. Repository secrets

Add all of these under Settings → Secrets and variables → Actions:

```
GROQ_API_KEY, GEMINI_API_KEY, GMAIL_CREDENTIALS, GMAIL_TOKEN_B64,
JOB_EMAIL, NOTIFY_EMAIL, FIRST_NAME, LAST_NAME, PHONE,
LINKEDIN_URL, GITHUB_URL, YEARS_EXP, SALARY_LPA, MIN_SALARY_LPA, EXPERIENCE_LEVEL
```

`PROXIES` is optional — leave unset unless you've bought a rotating-proxy plan.

## 4. Your resume

Replace `resume_base.docx` with your own resume. Requirements for the
tailoring code to work correctly:
- A "Professional Summary" (or "Summary"/"Profile") heading followed by
  paragraph(s) of summary text.
- A "Technical Skills" (or "Skills"/"Core Skills") heading followed by one
  paragraph per category, formatted as `Category: skill1, skill2, ...`.
- Section headings elsewhere (Work Experience, Certifications, Education,
  etc.) as short all-caps lines, or real Word Heading styles — either is
  detected correctly.

Then update `job_filters.py`'s `MATCH_KEYWORDS`, `TARGET_ROLES`, and
`LOCATIONS` to match your actual skills, target roles, and target cities —
these drive both what gets scraped and what confidence score each job gets.

## 5. Enable the workflows

Both `.github/workflows/job_sweep.yml` and `job_sweep_linkedin.yml` run on
`workflow_dispatch` too — trigger one manually from the Actions tab first to
confirm secrets are wired correctly before waiting for the cron schedule.

## Local testing

`cloud_run.py` is a plain script — run it directly without any of the GitHub
Actions machinery, as long as `credentials.json`/`token.json` exist locally
and the env vars above are set (a `.env` file works, loaded via
`python-dotenv` in `config.py`):

```bash
pip install -r requirements_cloud.txt
python cloud_run.py --sites indeed --hours-old 24
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| No email after a run that reports jobs found | Check the run's logs for `Email sent: False` — usually a Gmail token/credentials issue |
| Email stopped arriving after ~a week | OAuth consent screen still in "Testing" mode — see step 1 |
| `resume_score`/keyword coverage looks wrong | `job_filters.py`'s `MATCH_KEYWORDS` doesn't match your actual resume — see step 4 |
| Very few or no jobs found for a source | That source may be blocked — see README's "Known limitations" section |
