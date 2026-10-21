# Contributing to Hawkeye Jobs

Thank you for helping. Hawkeye Jobs is a free, non-profit job finder; contributions of any size are welcome — bug reports, translations, fixes for a site whose layout changed, or new features.

## The one rule

**Nobody may be harmed.** Every change has to keep these true:

- The app is polite to job sites: it keeps its per-site delays (`src/throttle.py`, `pipeline.POLITE_DELAYS`) and never hammers a site to go faster.
- The user's LinkedIn account is protected: LinkedIn posts run at most once a day.
- Nothing leaves the user's computer except the searches, the daily update check, and pages the user opens. No analytics, no telemetry, no server.
- A new job is never lost or silently skipped.

## Setup

Python 3.12 (3.10 or newer works) and Google Chrome.

```bash
python3.12 -m venv venv
venv/bin/python3 -m pip install -r requirements-dev.txt
venv/bin/python3 src/app.py            # the desktop app
```

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first: it explains every file and the reasons behind non-obvious decisions.

## Before you open a pull request

```bash
venv/bin/python3 -m unittest discover -s tests -v   # also runs the JavaScript tests when Node is installed
```

- Tests never touch real user files, the network, Chrome, a window or notifications. Every test file starts with `import _bootstrap` (it points the data folder at a temp directory).
- Window text lives in `src/web/static/i18n/ar.json` and `en.json` with identical keys. Arabic text is formal Arabic (فصحى), never colloquial.
- When you try the app with real searches, use a scratch data folder: `HAWKEYE_DATA_DIR=/tmp/hawkeye-dev`.
- Never commit credentials, `.env`, `config.json` or `hawkeye.db`.

## Reporting a broken site

Job sites change their pages often. If a source suddenly finds nothing, open an issue with the source, the country and keyword, and the relevant lines from `hawkeye.log` (Settings → Report a problem copies them with personal data removed).
