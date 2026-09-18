# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Full Architecture Reference

**Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before making any changes.** It documents every source file, function, HTML selector, and the reasoning behind non-obvious decisions.

## What this project does

hawkeye-jobs is an automated Flutter job hunter. It scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts, deduplicates results against `seen_jobs.json`, and emails new listings as an HTML digest. It runs every 2 hours via a macOS LaunchAgent.

## Running the scraper

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in GMAIL_USER / GMAIL_PASS / RECIPIENT_EMAIL
bash run.sh
```

Logs append to `hawkeye.log` in the project root (gitignored).

## Dependencies

```bash
pip install -r requirements.txt
# python-jobspy==1.1.80, pandas, beautifulsoup4, undetected-chromedriver, setuptools
```

No test suite. No linter.

## Pipeline (linear, 3 stages)

1. **`src/scraper.py`** — JobSpy scrapes LinkedIn + Indeed (no browser). Filters to Flutter/Dart/Mobile titles only. 48h window.
2. **`src/browser.py`** + Selenium scrapers — one shared Chrome instance (headless=False, required for Cloudflare):
   - `src/bayt.py` — Bayt.com (Jordan + Gulf)
   - `src/naukrigulf.py` — NaukriGulf (Gulf)
   - `src/google_jobs.py` — Google Jobs (Gulf, Europe, Americas, Remote)
   - `src/linkedin_posts.py` — LinkedIn **content/post** search ("we're hiring flutter" style hiring posts). Requires a logged-in LinkedIn session via a persistent Chrome profile (`--user-data-dir`, set by `LINKEDIN_PROFILE_DIR`, default `~/.hawkeye-linkedin-profile`). Log in once by hand; the session is reused every run.
3. **`src/tracker.py`** — deduplicates against `seen_jobs.json` using MD5(clean_url). **`src/email_sender.py`** — Gmail SMTP SSL HTML email.

## Credentials

Set in `.env` (gitignored): `GMAIL_USER`, `GMAIL_PASS`, `RECIPIENT_EMAIL`. Copy from `.env.example`. Never commit real credentials.

## Key files to know

| File | What to touch when... |
|---|---|
| `src/scraper.py` | Add LinkedIn/Indeed searches, add/remove title exclusions |
| `src/bayt.py` | Add Bayt search terms/countries, fix broken selectors |
| `src/naukrigulf.py` | Add NaukriGulf searches, fix broken selectors |
| `src/google_jobs.py` | Add Google Jobs searches, fix broken selectors |
| `src/linkedin_posts.py` | Add/edit LinkedIn hiring-post search phrases, fix post selectors, adjust hiring-signal keywords |
| `src/browser.py` | Chrome version changed (`version_main=149`), persistent-profile (`user_data_dir`) |
| `src/utils.py` | Date parsing logic, URL cleaning |
| `src/email_sender.py` | Email template, source colors |

## macOS LaunchAgent

```bash
launchctl list | grep hawkeye
launchctl unload ~/Library/LaunchAgents/com.hawkeye-jobs.plist
launchctl load  ~/Library/LaunchAgents/com.hawkeye-jobs.plist
bash uninstall_cron.sh
```

## Resetting seen jobs

```bash
rm -f seen_jobs.json
```
