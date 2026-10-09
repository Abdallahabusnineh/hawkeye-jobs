# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Full Architecture Reference

**Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before making any changes.** It documents every source file, function, HTML selector, and the reasoning behind non-obvious decisions.

## What this project does

hawkeye-jobs is a terminal job hunter. The user picks keywords, locations, and sources; it scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts; deduplicates against the SQLite job store; prints new listings; and emails an HTML digest. It runs only when the user starts it.

## Running the scraper

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in GMAIL_USER / GMAIL_PASS / RECIPIENT_EMAIL
bash run.sh
```

Runtime files (`hawkeye.db`, `config.json`, `hawkeye.log`, LinkedIn profile) live in the per-OS data directory from `src/paths.py` (`HAWKEYE_DATA_DIR` overrides it).

## Dependencies

```bash
pip install -r requirements.txt
# python-jobspy==1.1.80, pandas, requests, beautifulsoup4, undetected-chromedriver, setuptools
```

Tests: `venv/bin/python3 -m unittest discover -s tests -v`

## Pipeline

1. **`src/settings.py`** — reuse or prompt for keywords / countries / websites (`src/picker.py`). Countries come from `src/countries.py`.
2. **`src/catalog.py`** — expand those choices into per-site queries.
3. **`src/scraper.py`** — JobSpy `fetch_linkedin` / `fetch_indeed` (no browser). 48h window.
4. **`src/browser.py`** + Selenium scrapers when those sources are selected:
   - `src/bayt.py` — Bayt.com (Jordan + Gulf only)
   - `src/naukrigulf.py` — NaukriGulf (Gulf only)
   - `src/google_jobs.py` — Google Jobs
   - `src/linkedin_posts.py` — LinkedIn hiring posts. Needs a logged-in Chrome profile (`paths.linkedin_profile_dir()`: `LINKEDIN_PROFILE_DIR`, else an existing `~/.hawkeye-linkedin-profile`, else `<data dir>/linkedin-profile`).
5. **`src/pipeline.py`** — `run_search()`: plans queries (`catalog.plan_queries`), runs an HTTP lane (JobSpy) and a browser lane (Chrome) in parallel, saves each query's jobs to **`src/store.py`** (SQLite, statuses), emits progress events, supports cancel. **`src/filters.py`** — title/post matching. **`src/email_sender.py`** — Gmail SMTP.

## Credentials

Set in `.env` (gitignored): `GMAIL_USER`, `GMAIL_PASS`, `RECIPIENT_EMAIL`. Copy from `.env.example`. Never commit real credentials.

## Key files to know

| File | What to touch when... |
|---|---|
| `src/catalog.py` | Per-site location slugs (Bayt/Naukri/Indeed aliases) |
| `src/countries.py` | World country list (public API on every run) |
| `src/settings.py` | Prompt / config.json behaviour |
| `src/picker.py` | Arrow / space / type-to-search selection UI |
| `src/filters.py` | Title matching / hiring-post signals |
| `src/scraper.py` | LinkedIn/Indeed JobSpy calls |
| `src/bayt.py` | Bayt selectors |
| `src/naukrigulf.py` | NaukriGulf selectors |
| `src/google_jobs.py` | Google Jobs selectors |
| `src/linkedin_posts.py` | LinkedIn post selectors |
| `src/browser.py` | Chrome discovery (`find_chrome`), auto-detected version, `wait_for`, persistent profile |
| `src/pipeline.py` | Run orchestration, events, cancel |
| `src/store.py` | SQLite schema, job statuses, dedup |
| `src/paths.py` | Data directory and file locations |
| `src/utils.py` | Date parsing, URL cleaning |
| `src/email_sender.py` | Email template, source colors |

## Resetting

```bash
# macOS — Linux: ~/.local/share/hawkeye-jobs — Windows: %LOCALAPPDATA%\hawkeye-jobs
rm -f ~/Library/Application\ Support/hawkeye-jobs/hawkeye.db*
# also, if you used an older version (it is re-imported on every start otherwise):
rm -f seen_jobs.json
```
