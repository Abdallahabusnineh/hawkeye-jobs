# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Full Architecture Reference

**Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before making any changes.** It documents every source file, function, HTML selector, and the reasoning behind non-obvious decisions.

## What this project does

hawkeye-jobs is a terminal job hunter. The user picks keywords, locations, and sources; it scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf and Google Jobs; deduplicates against the SQLite job store; prints new listings; and emails an HTML digest. It runs only when the user starts it.

## Running the scraper

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in GMAIL_USER / GMAIL_PASS / RECIPIENT_EMAIL
bash run.sh
venv/bin/python3 src/app.py   # desktop app (--no-window to serve only)
```

Runtime files (`hawkeye.db`, `config.json`, `hawkeye.log`, LinkedIn profile) live in the per-OS data directory from `src/paths.py` (`HAWKEYE_DATA_DIR` overrides it).

## Dependencies

```bash
pip install -r requirements-dev.txt   # runtime pins (requirements.txt) + test client + PyInstaller
```

Use Python 3.12: python-jobspy pins numpy 1.26.3 (no wheels for newer Pythons). Releases: bump `src/version.py`, push tag `v<version>`; see docs/ARCHITECTURE.md → Packaging and releases.

Tests: `venv/bin/python3 -m unittest discover -s tests -v` (also runs `node --test tests/js/*.test.mjs` when node is installed)

Every test file must `import _bootstrap` (see tests/_bootstrap.py) — it points all runtime data at a temp folder; tests/test_isolation.py enforces it.

## Pipeline

1. **`src/settings.py`** — reuse or prompt for keywords / countries / websites (`src/picker.py`). Countries come from `src/countries.py`.
2. **`src/catalog.py`** — expand those choices into per-site queries.
3. **`src/scraper.py`** — JobSpy `fetch_linkedin` / `fetch_indeed` (no browser). Job-age window from the user's setting (7/14/30 days, `utils.set_job_age_days`).
4. **`src/browser.py`** + Selenium scrapers when those sources are selected:
   - `src/bayt.py` — Bayt.com (Jordan + Gulf only)
   - `src/naukrigulf.py` — NaukriGulf (Gulf only)
   - `src/google_jobs.py` — Google Jobs
5. **`src/pipeline.py`** — `run_search()`: plans queries (`catalog.plan_queries`), runs an HTTP lane (JobSpy) and a browser lane (Chrome) in parallel, saves each query's jobs to **`src/store.py`** (SQLite, statuses), emits progress events, supports cancel. **`src/filters.py`** — title matching. (LinkedIn hiring posts were removed in 1.2.0; see docs/ARCHITECTURE.md.) **`src/email_sender.py`** — Gmail SMTP.

## Credentials

Set in `.env` (gitignored): `GMAIL_USER`, `GMAIL_PASS`, `RECIPIENT_EMAIL`. Copy from `.env.example`. Never commit real credentials.

## Key files to know

| File | What to touch when... |
|---|---|
| `src/catalog.py` | Per-site location slugs (Bayt/Naukri/Indeed aliases) |
| `src/countries.py` | Country list bundled in `src/data/countries.json` (UN states + Palestine, Hong Kong, Macau; Arabic/English); rebuild with `tools/update_countries.py` |
| `src/settings.py` | Prompt / config.json behaviour |
| `src/picker.py` | Arrow / space / type-to-search selection UI |
| `src/filters.py` | Title matching |
| `src/scraper.py` | LinkedIn/Indeed JobSpy calls |
| `src/bayt.py` | Bayt selectors |
| `src/naukrigulf.py` | NaukriGulf selectors |
| `src/google_jobs.py` | Google Jobs selectors |
| `src/browser.py` | Chrome discovery (`find_chrome`), auto-detected version, `wait_for`, persistent profile |
| `src/cdp_browser.py` | Fallback when chromedriver cannot run (Smart App Control): Chrome over DevTools, no chromedriver |
| `src/pipeline.py` | Run orchestration, events, cancel |
| `src/store.py` | SQLite schema, job statuses, dedup |
| `src/paths.py` | Data directory and file locations |
| `src/utils.py` | Date parsing, URL cleaning |
| `src/email_sender.py` | Email template, source colors |
| `src/app.py` | Desktop launcher: free port, per-launch token, uvicorn thread, pywebview window, graceful shutdown |
| `src/runner.py` | `RunManager` (one search at a time, live events, `exclusive()`) and `AutoSearch` (timer) |
| `src/reporting.py` | Report new jobs through a notifier, then mark them reported |
| `src/notify.py` | Desktop notifications (never fails the run) |
| `src/run_lock.py` | `RunLock`: cross-process lock on `<data dir>/run.lock` |
| `src/background.py` | Start at login (`Autostart`) and one running app per user (`SingleInstance`); the tray and hide-on-close live in `app.py` |
| `src/version.py` | `__version__`, shown in Settings and compared with GitHub releases |
| `src/web/server.py` | FastAPI app: Host/token guards, static UI |
| `src/web/api.py` | The `/api/*` endpoints |
| `src/web/services.py` | `AppServices`: everything the API needs from outside (swappable in tests) |
| `src/web/redact.py` | Strips personal data from the problem-report log excerpt |
| `src/web/static/index.html` | The window's markup (Alpine.js `x-*` attributes; job text only via `x-text`) |
| `src/web/static/app.js` | The `hawkeye` Alpine component: pages, wizard, SSE handling |
| `src/web/static/logic.js` | Pure UI helpers (tested with `node --test tests/js/*.test.mjs`) |
| `src/web/static/api.js` | Local API client; moves the token from the URL to sessionStorage |
| `src/web/static/i18n/*.json` | All UI text, formal Arabic and English (identical keys; `tests/test_frontend.py`) |
| `packaging/hawkeye.spec` | PyInstaller build (what gets bundled); `packaging/launcher.py` is the frozen entry point |
| `packaging/smoke_test.py` | Checks a build: `--self-test`, then start/serve |
| `.github/workflows/release.yml` | Builds macOS/Windows/Linux on a `v*` tag and publishes the GitHub release |
| `site/index.html` | Download page (GitHub Pages, `.github/workflows/pages.yml`); `site/install.sh` / `site/install.ps1` are the one-command installers |
| `src/packaged.py` | Microsoft Store (MSIX) app: package detection, LocalState data folder, StartupTask, Store product id |
| `packaging/windows/build_msix.py` | Builds the Store MSIX (manifest `packaging/windows/msix/AppxManifest.xml`, logos); `test_msix.ps1` installs and smoke-tests it in CI; upload it to the Store by hand after a release |
| `packaging/windows/hawkeye.iss` | Windows installer (Inno Setup); `packaging/linux/` builds the AppImage |

## Resetting

```bash
# macOS — Linux: ~/.local/share/hawkeye-jobs — Windows: %LOCALAPPDATA%\hawkeye-jobs
rm -f ~/Library/Application\ Support/hawkeye-jobs/hawkeye.db*
# also, if you used an older version (it is re-imported on every start otherwise):
rm -f seen_jobs.json
```
