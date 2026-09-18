# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Full Architecture Reference

**Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before making any changes.** It documents every source file, function, HTML selector, and the reasoning behind non-obvious decisions.

## What this project does

hawkeye-jobs is a terminal job hunter. The user picks keywords, locations, and sources; it scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts; deduplicates against `seen_jobs.json`; prints new listings; and emails an HTML digest. It runs only when the user starts it.

## Running the scraper

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in GMAIL_USER / GMAIL_PASS / RECIPIENT_EMAIL
bash run.sh
```

Last search settings are stored in `config.json` (gitignored).

## Dependencies

```bash
pip install -r requirements.txt
# python-jobspy==1.1.80, pandas, beautifulsoup4, undetected-chromedriver, setuptools
```

Tests: `python3 -m unittest discover -s tests -v`

## Pipeline

1. **`src/settings.py`** — reuse or prompt for keywords / locations / sources.
2. **`src/catalog.py`** — expand those choices into per-site queries.
3. **`src/scraper.py`** — JobSpy scrapes LinkedIn + Indeed (no browser). 48h window.
4. **`src/browser.py`** + Selenium scrapers when those sources are selected:
   - `src/bayt.py` — Bayt.com (Jordan + Gulf only)
   - `src/naukrigulf.py` — NaukriGulf (Gulf only)
   - `src/google_jobs.py` — Google Jobs
   - `src/linkedin_posts.py` — LinkedIn hiring posts. Needs a logged-in Chrome profile (`LINKEDIN_PROFILE_DIR`, default `~/.hawkeye-linkedin-profile`).
5. **`src/filters.py`** — title/post matching from user keywords. **`src/tracker.py`** — MD5(clean_url) dedup. **`src/email_sender.py`** — Gmail SMTP.

## Credentials

Set in `.env` (gitignored): `GMAIL_USER`, `GMAIL_PASS`, `RECIPIENT_EMAIL`. Copy from `.env.example`. Never commit real credentials.

## Key files to know

| File | What to touch when... |
|---|---|
| `src/catalog.py` | Add countries or sources, fix per-site location slugs |
| `src/settings.py` | Prompt / config.json behaviour |
| `src/filters.py` | Title matching / hiring-post signals |
| `src/scraper.py` | LinkedIn/Indeed JobSpy calls |
| `src/bayt.py` | Bayt selectors |
| `src/naukrigulf.py` | NaukriGulf selectors |
| `src/google_jobs.py` | Google Jobs selectors |
| `src/linkedin_posts.py` | LinkedIn post selectors |
| `src/browser.py` | Chrome version (`version_main=149`), persistent-profile |
| `src/utils.py` | Date parsing, URL cleaning |
| `src/email_sender.py` | Email template, source colors |

## Resetting

```bash
rm -f seen_jobs.json
rm -f config.json
```
