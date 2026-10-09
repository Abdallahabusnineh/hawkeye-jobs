# Core Pipeline Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the terminal-only scraper into a shared core: `run_search()` with progress events and cancellation, a SQLite job store with statuses, a per-OS data directory, and faster parallel scraping. The terminal mode keeps working on top of it.

**Architecture:**
- `pipeline.run_search()` plans every query (`catalog.plan_queries`) and runs it on one of two threads: an HTTP lane (JobSpy: LinkedIn, Indeed) and a browser lane (Chrome: Bayt, NaukriGulf, Google Jobs, LinkedIn posts).
- Lanes only push messages onto a queue. The calling thread saves each query's jobs to `store.Store` (SQLite, WAL) and emits plain-data events.
- Each scraper exposes a single-query `fetch_*()` that raises on error and waits for job cards instead of sleeping.

**Tech Stack:** Python 3.11+ (the repo venv is 3.14), stdlib `sqlite3` / `threading` / `queue` / `logging`, `platformdirs`, selenium `WebDriverWait`, `undetected-chromedriver`, `python-jobspy`, BeautifulSoup. Tests use stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-10-core-pipeline-design.md`. Read it before starting. It holds the reasons behind each decision.

## Global Constraints

- **Running Python:** run everything with the repo venv: `venv/bin/python3`. **`venv/bin/pip` is broken** (stale shebang). Use `venv/bin/python3 -m pip ...`.
- **Test command:** `venv/bin/python3 -m unittest discover -s tests -v`. All 67 existing tests must keep passing after every task.
- **Tests:**
  - stdlib `unittest` only.
  - No network, no real Chrome.
  - Never write into the real data directory: use `tempfile` and `HAWKEYE_DATA_DIR`.
- **Dependencies:** the only new one is `platformdirs`, unpinned, like the others.
- **Repo root:** no runtime file may be written there. Everything goes in `paths.data_dir()`.
- **Data dir resolution:** `HAWKEYE_DATA_DIR`, else `platformdirs.user_data_dir("hawkeye-jobs", appauthor=False)`.
- **Job id:** `md5(clean_url(url).strip().rstrip("/"))`, byte-identical to the old `tracker._job_id`.
- **Job statuses:** exactly `new`, `saved`, `applied`, `dismissed`.
- **Run states:** exactly `running`, `done`, `cancelled`, `failed`.
- **Events** are dicts with a `type` key and data-only values. Never put English sentences in an event.
- **Smart-wait timeouts:** Bayt 20 s; NaukriGulf, Google Jobs, LinkedIn posts 15 s.
- **Log file:** `<data>/hawkeye.log`, `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)`.
- **Code style:**
  - Module docstrings use the `name.py — summary` + `====` underline style already in `src/`.
  - Loggers are `log = logging.getLogger(__name__)`.
  - Tests start with `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))`, like the existing ones.
- **Broken window:** from Task 6 until Task 10, `bash run.sh` is broken, because `main.py` still imports the removed `scrape_*` functions. Do not run it in between. Unit tests must pass throughout.

## Review Focus

1. **Zero queries:** a user picks Jordan + NaukriGulf only (NaukriGulf has no Jordan). The run must finish at once with `total: 0` and `state: done`, not hang. Test: Task 9 `test_no_queries_finishes_immediately`.
2. **Ctrl+C or cancel mid-run:** new jobs already found must still be printed and emailed, because they are now marked seen and would otherwise never be reported. Test: Task 10 `test_cancelled_run_still_emails_new_jobs`.
3. **Same URL twice in one run** (e.g. LinkedIn returns one job for "java" and "java developer", or across the two lanes): it counts as new exactly once. Tests: Task 2 `test_duplicate_in_same_batch_is_new_once`, Task 9 `test_same_url_from_both_lanes_is_new_once`.
4. **Search text with `%` or `_`:** the user's search box text must match literally, not as SQL wildcards. Test: Task 2 `test_text_filter_is_literal`.
5. **Upgrading user:** someone with an old `seen_jobs.json` and `~/.hawkeye-linkedin-profile` must not get repeat jobs and must stay logged in. Tests: Task 1 `test_legacy_profile_used_when_present`, Task 10 `test_legacy_seen_jobs_are_not_reported_again`.

---

### Task 0: Measure the baseline run time

The spec requires a before/after timing. This must happen **before any code changes**, with the code as it is on `main`.

**Files:**
- Create: `docs/superpowers/plans/2026-10-10-core-pipeline-timings.md`

- [ ] **Step 1: Back up the seen-jobs file so the baseline run does not change it**

```bash
cp seen_jobs.json /tmp/hawkeye-seen-backup.json
```

- [ ] **Step 2: Time one full run with email disabled**

Empty `GMAIL_*` values make `send_email` skip sending. `_load_dotenv` uses `setdefault`, so the `.env` file will not override them. Piping `y` answers "Use these saved settings?". This opens Chrome windows and takes 10–20 minutes.

```bash
time (echo y | GMAIL_USER= GMAIL_PASS= venv/bin/python3 src/main.py) 2>&1 | tail -25
```

Expected: the run ends with `🦅 hawkeye-jobs done!` or `No new jobs to report`. Note the `real` time.

- [ ] **Step 3: Restore the seen-jobs file**

```bash
cp /tmp/hawkeye-seen-backup.json seen_jobs.json
```

- [ ] **Step 4: Record the number**

Create `docs/superpowers/plans/2026-10-10-core-pipeline-timings.md`:

```markdown
# Core pipeline — run timings

Same `config.json` both times (5 keywords × remote/jo/om/sa × all 6 sources = 80 queries).

| When | Commit | `real` time |
|---|---|---|
| Before (baseline) | <output of `git rev-parse --short HEAD`> | <e.g. 14m32s> |
| After | (filled in Task 11) | |
```

Fill in the two values with the actual commit and time.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-10-10-core-pipeline-timings.md
git commit -m "Record baseline run time before core refactor"
```

---

### Task 1: Data directory and logging (`paths.py`, `logs.py`)

**Files:**
- Create: `src/paths.py`, `src/logs.py`, `tests/test_paths.py`, `tests/test_logs.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces (used by Tasks 2, 3, 9, 10):
  - `paths.data_dir() -> Path`: creates the directory.
  - `paths.db_path() -> Path`, `paths.config_path() -> Path`, `paths.countries_cache_path() -> Path`, `paths.log_path() -> Path`.
  - `paths.linkedin_profile_dir() -> Path`.
  - `logs.setup_logging(level=logging.INFO) -> None`.

- [ ] **Step 1: Install the dependency**

Append one line to `requirements.txt`:

```
platformdirs
```

Run: `venv/bin/python3 -m pip install -r requirements.txt`
Expected: `Successfully installed platformdirs-...` (or "Requirement already satisfied").

- [ ] **Step 2: Write the failing tests**

Create `tests/test_paths.py`:

```python
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import paths


def _env_without(*names):
    return {k: v for k, v in os.environ.items() if k not in names}


class DataDirTests(unittest.TestCase):
    def test_env_override_is_used_and_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "data"
            with patch.dict(os.environ, {"HAWKEYE_DATA_DIR": str(target)}):
                self.assertEqual(paths.data_dir(), target)
                self.assertTrue(target.is_dir())
                self.assertEqual(paths.db_path(), target / "hawkeye.db")
                self.assertEqual(paths.config_path(), target / "config.json")
                self.assertEqual(paths.countries_cache_path(), target / "countries_cache.json")
                self.assertEqual(paths.log_path(), target / "hawkeye.log")

    def test_default_uses_platformdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, _env_without("HAWKEYE_DATA_DIR"), clear=True), \
                 patch("paths.platformdirs.user_data_dir", return_value=tmp) as user_data_dir:
                self.assertEqual(paths.data_dir(), Path(tmp))
            user_data_dir.assert_called_once_with("hawkeye-jobs", appauthor=False)


class LinkedInProfileTests(unittest.TestCase):
    def test_env_var_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom-profile"
            with patch.dict(os.environ, {"LINKEDIN_PROFILE_DIR": str(custom), "HAWKEYE_DATA_DIR": tmp}):
                self.assertEqual(paths.linkedin_profile_dir(), custom)

    def test_legacy_profile_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "old-profile"
            legacy.mkdir()
            env = _env_without("LINKEDIN_PROFILE_DIR")
            env["HAWKEYE_DATA_DIR"] = str(Path(tmp) / "data")
            with patch.dict(os.environ, env, clear=True), \
                 patch("paths.LEGACY_LINKEDIN_PROFILE", legacy):
                self.assertEqual(paths.linkedin_profile_dir(), legacy)

    def test_falls_back_to_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env_without("LINKEDIN_PROFILE_DIR")
            env["HAWKEYE_DATA_DIR"] = tmp
            with patch.dict(os.environ, env, clear=True), \
                 patch("paths.LEGACY_LINKEDIN_PROFILE", Path(tmp) / "missing"):
                self.assertEqual(paths.linkedin_profile_dir(), Path(tmp) / "linkedin-profile")


if __name__ == "__main__":
    unittest.main()
```

Create `tests/test_logs.py`:

```python
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import logs


class SetupLoggingTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler.name == logs.HANDLER_NAME:
                root.removeHandler(handler)
                handler.close()

    def test_writes_to_log_file_in_data_dir_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HAWKEYE_DATA_DIR": tmp}):
                logs.setup_logging()
                logs.setup_logging()
                ours = [h for h in logging.getLogger().handlers if h.name == logs.HANDLER_NAME]
                self.assertEqual(len(ours), 1)
                self.assertEqual(ours[0].maxBytes, 1_000_000)
                self.assertEqual(ours[0].backupCount, 3)
                logging.getLogger("hawkeye.test").info("hello log")
                ours[0].flush()
                self.assertIn("hello log", (Path(tmp) / "hawkeye.log").read_text(encoding="utf-8"))
                self.tearDown()


if __name__ == "__main__":
    unittest.main()
```

(`self.tearDown()` inside the `with` closes the handler before the temp dir is deleted. Windows cannot delete open files.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_paths tests.test_logs -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'paths'` (and `'logs'`).

- [ ] **Step 4: Write `src/paths.py`**

```python
"""
paths.py — Where hawkeye-jobs keeps its runtime files
======================================================
Every runtime file (database, config, country cache, log, LinkedIn Chrome
profile) lives in one per-OS data directory, never in the repo:

    macOS   : ~/Library/Application Support/hawkeye-jobs
    Windows : C:\\Users\\<user>\\AppData\\Local\\hawkeye-jobs
    Linux   : ~/.local/share/hawkeye-jobs   (honours XDG_DATA_HOME)

Set HAWKEYE_DATA_DIR to use another folder. Paths are resolved on every call
(not at import time) so tests and the app can change the env var.
"""

import os
from pathlib import Path

import platformdirs

APP_NAME = "hawkeye-jobs"
LEGACY_LINKEDIN_PROFILE = Path("~/.hawkeye-linkedin-profile")


def data_dir() -> Path:
    """Return the data directory, creating it if needed."""
    override = os.environ.get("HAWKEYE_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        path = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "hawkeye.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def countries_cache_path() -> Path:
    return data_dir() / "countries_cache.json"


def log_path() -> Path:
    return data_dir() / "hawkeye.log"


def linkedin_profile_dir() -> Path:
    """
    Chrome profile used for the LinkedIn login.

    LINKEDIN_PROFILE_DIR wins; otherwise the old ~/.hawkeye-linkedin-profile is
    kept if it exists (so existing users stay logged in); otherwise
    <data>/linkedin-profile.
    """
    override = os.environ.get("LINKEDIN_PROFILE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    legacy = LEGACY_LINKEDIN_PROFILE.expanduser()
    if legacy.exists():
        return legacy
    return data_dir() / "linkedin-profile"
```

- [ ] **Step 5: Write `src/logs.py`**

```python
"""
logs.py — Logging setup
========================
All modules log through `logging.getLogger(__name__)`. setup_logging() sends
those records to <data>/hawkeye.log (1 MB × 3 rotating files). A packaged app
has no terminal, so this file is what users attach to bug reports.
"""

import logging
from logging.handlers import RotatingFileHandler

from paths import log_path

HANDLER_NAME = "hawkeye-file"
_NOISY_LOGGERS = ("urllib3", "selenium", "undetected_chromedriver", "WDM")


def setup_logging(level=logging.INFO) -> None:
    """Attach the rotating file handler to the root logger. Calling it twice is harmless."""
    root = logging.getLogger()
    if any(handler.name == HANDLER_NAME for handler in root.handlers):
        return
    handler = RotatingFileHandler(
        log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.name = HANDLER_NAME
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all tests PASS (67 existing + 6 new).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt src/paths.py src/logs.py tests/test_paths.py tests/test_logs.py
git commit -m "Add per-OS data directory and rotating log file"
```

---

### Task 2: SQLite job store (`store.py`)

**Files:**
- Create: `src/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `paths.db_path()` (Task 1), `utils.clean_url(url) -> str` (existing).
- Produces (used by Tasks 9, 10):
  - `store.job_id(url: str) -> str`
  - `store.STATUSES`, `store.RUN_STATES`
  - `store.Store(path: Path | None = None)` with:
    - `.close()`, plus context-manager support
    - `.recover_interrupted_runs() -> int`
    - `.import_legacy_seen_file(path) -> int`
    - `.start_run(settings: dict) -> int`
    - `.add_jobs(run_id: int, jobs: list[dict]) -> list[dict]`: each returned dict is the input job plus `"id"`
    - `.finish_run(run_id: int, state: str, found: int, new: int) -> None`
    - `.set_status(job_id: str, status: str) -> bool`
    - `.list_jobs(status=None, source=None, run_id=None, text=None, include_dismissed=False, limit=100, offset=0) -> list[dict]`
    - `.list_runs(limit=20) -> list[dict]`: each dict has `settings` (parsed) instead of `settings_json`
- `add_jobs` reads optional `job["keyword"]` and `job["location_id"]` (the pipeline stamps them in Task 9).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store.py`:

```python
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from store import Store, job_id


def make_job(url, title="Java Developer", source="Bayt", **extra):
    job = {
        "title": title,
        "url": url,
        "description": "Acme - Amman",
        "source": source,
        "keyword": "Java",
        "location": "jordan",
        "location_id": "jo",
        "published": "1 day ago",
    }
    job.update(extra)
    return job


SETTINGS = {"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class JobIdTests(unittest.TestCase):
    def test_matches_old_tracker_hash(self):
        url = "https://www.bayt.com/en/jordan/jobs/java-123"
        self.assertEqual(job_id(url), hashlib.md5(url.encode()).hexdigest())

    def test_ignores_tracking_params_and_trailing_slash(self):
        base = job_id("https://indeed.com/viewjob?jk=abc")
        self.assertEqual(job_id("https://indeed.com/viewjob?jk=abc&utm_source=google"), base)
        self.assertEqual(job_id("https://x.com/a/"), job_id("https://x.com/a"))


class SchemaTests(StoreTestCase):
    def test_schema_version_and_wal(self):
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        conn.close()

    def test_reopening_existing_db_keeps_data(self):
        run_id = self.store.start_run(SETTINGS)
        self.store.add_jobs(run_id, [make_job("https://a.com/1")])
        self.store.close()
        self.store = Store(self.db)
        self.assertEqual(len(self.store.list_jobs()), 1)


class AddJobsTests(StoreTestCase):
    def test_returns_only_new_jobs(self):
        run1 = self.store.start_run(SETTINGS)
        first = self.store.add_jobs(run1, [make_job("https://a.com/1"), make_job("https://a.com/2")])
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0]["id"], job_id("https://a.com/1"))
        run2 = self.store.start_run(SETTINGS)
        second = self.store.add_jobs(run2, [make_job("https://a.com/2"), make_job("https://a.com/3")])
        self.assertEqual([j["url"] for j in second], ["https://a.com/3"])

    def test_reseen_job_keeps_status_and_updates_last_seen(self):
        run1 = self.store.start_run(SETTINGS)
        with patch("store._now", return_value="2026-01-01T00:00:00Z"):
            self.store.add_jobs(run1, [make_job("https://a.com/1")])
        self.store.set_status(job_id("https://a.com/1"), "saved")
        run2 = self.store.start_run(SETTINGS)
        with patch("store._now", return_value="2026-01-02T00:00:00Z"):
            self.assertEqual(self.store.add_jobs(run2, [make_job("https://a.com/1")]), [])
        row = self.store.list_jobs()[0]
        self.assertEqual(row["status"], "saved")
        self.assertEqual(row["first_seen_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(row["last_seen_at"], "2026-01-02T00:00:00Z")
        self.assertEqual(row["first_run_id"], run1)

    def test_duplicate_in_same_batch_is_new_once(self):
        run_id = self.store.start_run(SETTINGS)
        new = self.store.add_jobs(run_id, [
            make_job("https://a.com/1"),
            make_job("https://a.com/1?utm_source=x"),
        ])
        self.assertEqual(len(new), 1)
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_jobs_without_url_are_ignored(self):
        run_id = self.store.start_run(SETTINGS)
        self.assertEqual(self.store.add_jobs(run_id, [make_job("")]), [])
        self.assertEqual(self.store.list_jobs(), [])

    def test_stores_keyword_and_location_id(self):
        run_id = self.store.start_run(SETTINGS)
        self.store.add_jobs(run_id, [make_job("https://a.com/1", keyword="Kotlin", location_id="ae")])
        row = self.store.list_jobs()[0]
        self.assertEqual((row["keyword"], row["location_id"], row["status"]), ("Kotlin", "ae", "new"))


class LegacySeenTests(StoreTestCase):
    def test_legacy_hash_blocks_job_and_import_is_idempotent(self):
        legacy = Path(self.tmp.name) / "seen_jobs.json"
        legacy.write_text(json.dumps({"seen": [job_id("https://a.com/old")], "count": 1}), encoding="utf-8")
        self.assertEqual(self.store.import_legacy_seen_file(legacy), 1)
        self.assertEqual(self.store.import_legacy_seen_file(legacy), 0)
        run_id = self.store.start_run(SETTINGS)
        new = self.store.add_jobs(run_id, [make_job("https://a.com/old"), make_job("https://a.com/fresh")])
        self.assertEqual([j["url"] for j in new], ["https://a.com/fresh"])

    def test_missing_or_corrupt_file_imports_nothing(self):
        self.assertEqual(self.store.import_legacy_seen_file(Path(self.tmp.name) / "nope.json"), 0)
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.import_legacy_seen_file(bad), 0)


class StatusAndListTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.run_id = self.store.start_run(SETTINGS)
        self.store.add_jobs(self.run_id, [
            make_job("https://a.com/1", title="Java Developer", source="Bayt"),
            make_job("https://a.com/2", title="Kotlin 100% Remote", source="Linkedin"),
            make_job("https://a.com/3", title="Java_Lead", source="Bayt"),
        ])

    def test_set_status_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            self.store.set_status(job_id("https://a.com/1"), "archived")

    def test_set_status_on_unknown_job_returns_false(self):
        self.assertFalse(self.store.set_status("nope", "saved"))
        self.assertTrue(self.store.set_status(job_id("https://a.com/1"), "applied"))

    def test_dismissed_hidden_by_default(self):
        self.store.set_status(job_id("https://a.com/1"), "dismissed")
        urls = {j["url"] for j in self.store.list_jobs()}
        self.assertEqual(urls, {"https://a.com/2", "https://a.com/3"})
        self.assertEqual(len(self.store.list_jobs(include_dismissed=True)), 3)
        self.assertEqual([j["url"] for j in self.store.list_jobs(status="dismissed")], ["https://a.com/1"])

    def test_source_and_run_filters(self):
        self.assertEqual(len(self.store.list_jobs(source="Bayt")), 2)
        self.assertEqual(len(self.store.list_jobs(run_id=self.run_id)), 3)
        self.assertEqual(self.store.list_jobs(run_id=self.run_id + 1), [])

    def test_text_filter_is_literal(self):
        self.assertEqual([j["url"] for j in self.store.list_jobs(text="100%")], ["https://a.com/2"])
        self.assertEqual([j["url"] for j in self.store.list_jobs(text="a_L")], ["https://a.com/3"])
        self.assertEqual(self.store.list_jobs(text="%"), self.store.list_jobs(text="100%"))

    def test_newest_first(self):
        run2 = self.store.start_run(SETTINGS)
        with patch("store._now", return_value="2999-01-01T00:00:00Z"):
            self.store.add_jobs(run2, [make_job("https://a.com/latest")])
        self.assertEqual(self.store.list_jobs()[0]["url"], "https://a.com/latest")

    def test_list_jobs_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            self.store.list_jobs(status="archived")


class RunTests(StoreTestCase):
    def test_finish_run_and_list_runs(self):
        run_id = self.store.start_run(SETTINGS)
        self.store.finish_run(run_id, "done", found=5, new=2)
        runs = self.store.list_runs()
        self.assertEqual(runs[0]["id"], run_id)
        self.assertEqual(runs[0]["state"], "done")
        self.assertEqual((runs[0]["found_count"], runs[0]["new_count"]), (5, 2))
        self.assertEqual(runs[0]["settings"], SETTINGS)
        self.assertIsNotNone(runs[0]["finished_at"])

    def test_finish_run_rejects_bad_state(self):
        run_id = self.store.start_run(SETTINGS)
        with self.assertRaises(ValueError):
            self.store.finish_run(run_id, "running", 0, 0)

    def test_recover_interrupted_runs(self):
        stuck = self.store.start_run(SETTINGS)
        finished = self.store.start_run(SETTINGS)
        self.store.finish_run(finished, "done", 0, 0)
        self.assertEqual(self.store.recover_interrupted_runs(), 1)
        states = {r["id"]: r["state"] for r in self.store.list_runs()}
        self.assertEqual(states, {stuck: "failed", finished: "done"})


class ConcurrencyTests(StoreTestCase):
    def test_second_connection_sees_writes(self):
        reader = Store(self.db)
        try:
            run_id = self.store.start_run(SETTINGS)
            self.store.add_jobs(run_id, [make_job("https://a.com/1")])
            self.assertEqual(len(reader.list_jobs()), 1)
        finally:
            reader.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_store -v`
Expected: ERROR `ModuleNotFoundError: No module named 'store'`.

- [ ] **Step 3: Write `src/store.py`**

```python
"""
store.py — SQLite store for jobs, their statuses, and search runs
==================================================================
Replaces tracker.py / seen_jobs.json. Keeps every job found (not just a hash)
so the app can show history and let the user mark jobs as saved / applied /
dismissed.

Tables (see docs/superpowers/specs/2026-10-10-core-pipeline-design.md §5):
    runs        — one row per search run
    jobs        — one row per unique job URL (id = job_id(url))
    legacy_seen — hashes imported from the old seen_jobs.json (dedup only)

Threading:
    One Store per thread (sqlite3 connections are not shared). WAL mode lets
    the app read while a search writes. Call recover_interrupted_runs() once
    at process start — never while a search is running.
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from paths import db_path
from utils import clean_url

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STATUSES = ("new", "saved", "applied", "dismissed")
RUN_STATES = ("running", "done", "cancelled", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  state         TEXT NOT NULL CHECK (state IN ('running','done','cancelled','failed')),
  settings_json TEXT NOT NULL,
  found_count   INTEGER NOT NULL DEFAULT 0,
  new_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
  id                TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  url               TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  source            TEXT NOT NULL,
  location          TEXT NOT NULL DEFAULT '',
  location_id       TEXT,
  keyword           TEXT NOT NULL DEFAULT '',
  published         TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','saved','applied','dismissed')),
  status_changed_at TEXT,
  first_seen_at     TEXT NOT NULL,
  last_seen_at      TEXT NOT NULL,
  first_run_id      INTEGER REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS jobs_by_status_seen ON jobs (status, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS legacy_seen (id TEXT PRIMARY KEY);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_id(url: str) -> str:
    """MD5 of the cleaned URL — identical to the old tracker._job_id, so seen_jobs.json hashes still match."""
    cleaned = clean_url(url or "").strip().rstrip("/")
    return hashlib.md5(cleaned.encode()).hexdigest()


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else db_path()
        self.conn = sqlite3.connect(self.path, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        self.conn.executescript(_SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── runs ──────────────────────────────────────────────────────────────

    def recover_interrupted_runs(self) -> int:
        """Mark runs left 'running' by a crashed/killed process as 'failed'."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE runs SET state = 'failed', finished_at = ? WHERE state = 'running'",
                (_now(),),
            )
        if cur.rowcount:
            log.info("Marked %d interrupted run(s) as failed", cur.rowcount)
        return cur.rowcount

    def start_run(self, settings: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs (started_at, state, settings_json) VALUES (?, 'running', ?)",
                (_now(), json.dumps(settings, ensure_ascii=False)),
            )
        return cur.lastrowid

    def finish_run(self, run_id: int, state: str, found: int, new: int) -> None:
        if state not in RUN_STATES or state == "running":
            raise ValueError(f"Not a final run state: {state}")
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET state = ?, finished_at = ?, found_count = ?, new_count = ? WHERE id = ?",
                (state, _now(), found, new, run_id),
            )

    def list_runs(self, limit: int = 20) -> list:
        rows = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["settings"] = json.loads(item.pop("settings_json"))
            result.append(item)
        return result

    # ── jobs ──────────────────────────────────────────────────────────────

    def import_legacy_seen_file(self, path) -> int:
        """Import hashes from the old seen_jobs.json. Safe to call every start. Returns how many were added."""
        path = Path(path)
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Could not read legacy seen-jobs file %s", path)
            return 0
        seen = data.get("seen") if isinstance(data, dict) else None
        hashes = [h for h in (seen or []) if isinstance(h, str) and h]
        with self.conn:
            before = self.conn.total_changes
            self.conn.executemany(
                "INSERT OR IGNORE INTO legacy_seen (id) VALUES (?)", [(h,) for h in hashes]
            )
            added = self.conn.total_changes - before
        if added:
            log.info("Imported %d previously seen job(s) from %s", added, path)
        return added

    def add_jobs(self, run_id: int, jobs: list) -> list:
        """Save jobs from one query. Returns only jobs never seen before (each with an "id" key)."""
        now = _now()
        new_jobs = []
        with self.conn:
            for job in jobs:
                url = str(job.get("url") or "").strip()
                if not url:
                    continue
                jid = job_id(url)
                if self.conn.execute("SELECT 1 FROM legacy_seen WHERE id = ?", (jid,)).fetchone():
                    continue
                updated = self.conn.execute(
                    "UPDATE jobs SET last_seen_at = ? WHERE id = ?", (now, jid)
                )
                if updated.rowcount:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO jobs (id, title, url, description, source, location, location_id,
                                      keyword, published, status, first_seen_at, last_seen_at, first_run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
                    """,
                    (
                        jid,
                        str(job.get("title") or ""),
                        url,
                        str(job.get("description") or ""),
                        str(job.get("source") or ""),
                        str(job.get("location") or ""),
                        job.get("location_id"),
                        str(job.get("keyword") or ""),
                        str(job.get("published") or ""),
                        now,
                        now,
                        run_id,
                    ),
                )
                new_jobs.append({**job, "id": jid})
        return new_jobs

    def set_status(self, job_id: str, status: str) -> bool:
        if status not in STATUSES:
            raise ValueError(f"Unknown status: {status}")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE jobs SET status = ?, status_changed_at = ? WHERE id = ?",
                (status, _now(), job_id),
            )
        return cur.rowcount > 0

    def list_jobs(self, status=None, source=None, run_id=None, text=None,
                  include_dismissed=False, limit=100, offset=0) -> list:
        clauses, params = [], []
        if status is not None:
            if status not in STATUSES:
                raise ValueError(f"Unknown status: {status}")
            clauses.append("status = ?")
            params.append(status)
        elif not include_dismissed:
            clauses.append("status != 'dismissed'")
        if source:
            clauses.append("source = ?")
            params.append(source)
        if run_id is not None:
            clauses.append("first_run_id = ?")
            params.append(run_id)
        if text:
            clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            pattern = _like_pattern(text)
            params += [pattern, pattern]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY first_seen_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store.py tests/test_store.py
git commit -m "Add SQLite job store with statuses, runs and legacy seen-jobs import"
```

---

### Task 3: Config and country cache move to the data directory

**Files:**
- Modify: `src/settings.py` (imports at top; `DEFAULT_CONFIG_PATH`; `load_config`, `save_config`, `collect_settings`; new `migrate_legacy_config`)
- Modify: `src/countries.py:14-16` (`ROOT`, `DEFAULT_CACHE`) and `load_countries`
- Test: `tests/test_settings.py`, `tests/test_countries.py`

**Interfaces:**
- Consumes: `paths.config_path()`, `paths.countries_cache_path()` (Task 1).
- Produces (used by Task 10):
  - `settings.migrate_legacy_config(root: Path = ROOT, target: Path | None = None) -> bool`
  - `settings.load_config(path=None)`, `settings.save_config(data, path=None)`, `settings.collect_settings(path=None, interactive=None, countries=None)`, all defaulting to `paths.config_path()`.
  - `countries.load_countries(fetcher=None, cache_path=None)`, defaulting to `paths.countries_cache_path()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings.py`, above `if __name__ == "__main__":`. Also add `import os` to its imports.

```python
class DataDirConfigTests(unittest.TestCase):
    def test_save_config_keeps_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"keywords": ["Old"], "app": {"language": "ar"}}), encoding="utf-8")
            settings.save_config({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}, path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["app"], {"language": "ar"})
            self.assertEqual(saved["keywords"], ["Java"])
            self.assertEqual(saved["sources"], ["bayt"])

    def test_save_config_overwrites_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{broken", encoding="utf-8")
            data = {"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}
            settings.save_config(data, path)
            self.assertEqual(settings.load_config(path), data)

    def test_default_path_is_in_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HAWKEYE_DATA_DIR": tmp}):
                data = {"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}
                settings.save_config(data)
                self.assertTrue((Path(tmp) / "config.json").exists())
                self.assertEqual(settings.load_config(), data)

    def test_migrate_legacy_config_copies_once_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            legacy = root / "config.json"
            legacy.write_text(json.dumps({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}), encoding="utf-8")
            target = Path(tmp) / "data" / "config.json"
            target.parent.mkdir()
            self.assertTrue(settings.migrate_legacy_config(root, target))
            self.assertTrue(legacy.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), legacy.read_text(encoding="utf-8"))
            self.assertFalse(settings.migrate_legacy_config(root, target))

    def test_migrate_never_overwrites_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text('{"keywords": ["Old"]}', encoding="utf-8")
            target = root / "data-config.json"
            target.write_text('{"keywords": ["New"]}', encoding="utf-8")
            self.assertFalse(settings.migrate_legacy_config(root, target))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"keywords": ["New"]})
```

Append to `tests/test_countries.py`, above `if __name__ == "__main__":`. Add `import os` and `from unittest.mock import patch` to its imports if missing.

```python
class CountryCachePathTests(unittest.TestCase):
    def test_default_cache_lives_in_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HAWKEYE_DATA_DIR": tmp}):
                load_countries(fetcher=lambda url: SAMPLE_API)
            self.assertTrue((Path(tmp) / "countries_cache.json").exists())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_settings tests.test_countries -v`
Expected:
- FAIL: `test_save_config_keeps_unknown_keys` (`KeyError: 'app'`).
- FAIL: `test_default_path_is_in_data_dir`.
- ERROR: `migrate_legacy_config` tests (`AttributeError`).
- FAIL: `test_default_cache_lives_in_data_dir`.

- [ ] **Step 3: Update `src/settings.py`**

Replace the import block and `DEFAULT_CONFIG_PATH` at the top of the file with:

```python
"""Interactive search settings: prompt once, reuse from config.json next time."""

import json
import logging
import shutil
import sys
from pathlib import Path

from catalog import SOURCE_ORDER, SOURCES, get_location, normalize_location_id, source_choice_label
from countries import CountryFetchError, load_countries
from paths import config_path
from picker import color_error, color_hint, color_question, color_selected, confirmed_line, pick_many, pick_one, pick_searchable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SEARCH_KEYS = ("keywords", "locations", "sources")
```

Replace `load_config` and `save_config` with:

```python
def load_config(path=None):
    path = Path(path) if path else config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    keywords = data.get("keywords") or []
    locations = data.get("locations") or []
    sources = data.get("sources") or []
    if not keywords or not locations or not sources:
        return None
    return {
        "keywords": list(keywords),
        "locations": list(locations),
        "sources": list(sources),
    }


def save_config(data: dict, path=None) -> None:
    """Write keywords/locations/sources, keeping any other keys already in the file (e.g. the app's settings)."""
    path = Path(path) if path else config_path()
    existing = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}
    for key in SEARCH_KEYS:
        existing[key] = list(data[key])
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def migrate_legacy_config(root: Path = ROOT, target=None) -> bool:
    """Copy <repo>/config.json into the data directory once. The original stays where it is."""
    target = Path(target) if target else config_path()
    legacy = Path(root) / "config.json"
    if target.exists() or not legacy.exists():
        return False
    shutil.copy2(legacy, target)
    log.info("Copied legacy config %s to %s", legacy, target)
    return True
```

Change the `collect_settings` signature and its first lines:

```python
def collect_settings(path=None, interactive=None, countries=None) -> dict:
    """Return settings from a reuse prompt, or ask and save a new config."""
    path = Path(path) if path else config_path()
    if interactive is None:
        interactive = can_use_picker()
```

The rest of `collect_settings` is unchanged. It already passes `path` to `load_config` / `save_config` and prints `path.name`.

- [ ] **Step 4: Update `src/countries.py`**

Remove these two lines:

```python
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ROOT / "countries_cache.json"
```

Add `from paths import countries_cache_path` below the existing imports. In `load_countries`, replace `cache_path = cache_path or DEFAULT_CACHE` with:

```python
    cache_path = cache_path or countries_cache_path()
```

- [ ] **Step 5: Check nothing else used the removed names**

Run: `grep -rn "DEFAULT_CONFIG_PATH\|DEFAULT_CACHE" src tests`
Expected: no output.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/settings.py src/countries.py tests/test_settings.py tests/test_countries.py
git commit -m "Store config and country cache in the data directory; merge config on save"
```

---

### Task 4: Flat query plan (`catalog.plan_queries`)

**Files:**
- Modify: `src/catalog.py` (replace `expand_searches`, add `plan_queries`)
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces (used by Task 9): `catalog.plan_queries(keywords, location_ids, source_ids, country_labels=None) -> list[dict]`. Each dict is `{"source": str, "keyword": str, "location_id": str | None, "args": <the item expand_searches lists today>}`.
- `catalog.expand_searches(...)` keeps its exact current output.

- [ ] **Step 1: Write the failing tests**

In `tests/test_catalog.py`, change the import to `from catalog import expand_searches, plan_queries, source_choice_label` and add this class above `if __name__ == "__main__":`:

```python
class PlanQueriesTests(unittest.TestCase):
    def test_carries_location_id_keyword_and_args(self):
        plan = plan_queries(["Java"], ["jordan", "remote"], ["linkedin", "bayt"])
        self.assertEqual(plan, [
            {"source": "linkedin", "keyword": "Java", "location_id": "jo",
             "args": {"term": "Java", "location": "Jordan"}},
            {"source": "bayt", "keyword": "Java", "location_id": "jo",
             "args": ("java", "jordan")},
            {"source": "linkedin", "keyword": "Java", "location_id": "remote",
             "args": {"term": "Java", "location": "Remote"}},
        ])

    def test_linkedin_posts_have_no_location(self):
        plan = plan_queries(["Java"], ["jo", "ae"], ["linkedin_posts"])
        self.assertEqual(plan, [
            {"source": "linkedin_posts", "keyword": "Java", "location_id": None, "args": "hiring Java"},
            {"source": "linkedin_posts", "keyword": "Java", "location_id": None, "args": "looking for Java"},
        ])

    def test_unsupported_combination_plans_nothing(self):
        self.assertEqual(plan_queries(["Java"], ["jo"], ["naukrigulf"]), [])

    def test_expand_searches_is_plan_grouped_by_source(self):
        args = (["Java", "Kotlin"], ["jo", "ae", "remote"], list(SOURCE_IDS))
        grouped = {}
        for query in plan_queries(*args):
            grouped.setdefault(query["source"], []).append(query["args"])
        searches = expand_searches(*args)
        for source in SOURCE_IDS:
            self.assertEqual(searches[source], grouped.get(source, []))


SOURCE_IDS = ("linkedin", "indeed", "bayt", "naukrigulf", "google_jobs", "linkedin_posts")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_catalog -v`
Expected: ERROR `ImportError: cannot import name 'plan_queries'`.

- [ ] **Step 3: Replace `expand_searches` in `src/catalog.py`**

Replace the whole `expand_searches` function, from `def expand_searches(` to the end of the file, with:

```python
def plan_queries(keywords: list, location_ids: list, source_ids: list, country_labels=None) -> list:
    """
    Every query to run, in order, as {"source", "keyword", "location_id", "args"}.

    `args` is exactly what that source's fetch function takes. `location_id` is the
    normalised country id ('jo', 'remote', ...), or None for LinkedIn posts, which
    are not location-scoped. The UI uses location_id to show a translated country name.
    """
    selected = set(source_ids)
    keywords = [kw.strip() for kw in keywords if str(kw).strip()]
    country_labels = country_labels or {}
    queries = []

    def add(source, keyword, location_id, args):
        queries.append({"source": source, "keyword": keyword, "location_id": location_id, "args": args})

    for loc_id in location_ids:
        nid = normalize_location_id(loc_id)
        loc = get_location(loc_id, country_labels.get(nid) or country_labels.get(loc_id))
        if not loc:
            continue
        for keyword in keywords:
            if "linkedin" in selected and loc.get("linkedin"):
                add("linkedin", keyword, nid, {"term": keyword, "location": loc["linkedin"]})
            if "indeed" in selected and loc.get("indeed"):
                city, country = loc["indeed"]
                add("indeed", keyword, nid, {"term": keyword, "location": city, "country": country})
            if "bayt" in selected and loc.get("bayt"):
                add("bayt", keyword, nid, (_slug(keyword), loc["bayt"]))
            if "naukrigulf" in selected and loc.get("naukrigulf"):
                add("naukrigulf", keyword, nid, (keyword.lower(), loc["naukrigulf"]))
            if "google_jobs" in selected:
                if nid == "remote":
                    add("google_jobs", keyword, nid, (f"{keyword} remote", ""))
                elif loc.get("google") is not None:
                    add("google_jobs", keyword, nid, (keyword, loc["google"]))

    if "linkedin_posts" in selected:
        for keyword in keywords:
            add("linkedin_posts", keyword, None, f"hiring {keyword}")
            add("linkedin_posts", keyword, None, f"looking for {keyword}")

    return queries


def expand_searches(keywords: list, location_ids: list, source_ids: list, country_labels=None) -> dict:
    """Build per-source query lists from the user's keywords, locations, and sources."""
    searches = _empty_searches()
    for query in plan_queries(keywords, location_ids, source_ids, country_labels):
        searches[query["source"]].append(query["args"])
    return searches
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS, including every pre-existing `ExpandSearchesTests` test, unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/catalog.py tests/test_catalog.py
git commit -m "Add plan_queries: flat query list with country id per query"
```

---

### Task 5: Chrome discovery, smart waits, `ChromeNotFound` (`browser.py`)

**Files:**
- Modify: `src/browser.py` (whole file)
- Create: `tests/test_browser.py`

**Interfaces:**
- Produces (used by Tasks 7, 8, 9):
  - `browser.ChromeNotFound(RuntimeError)`
  - `browser.find_chrome() -> str | None`
  - `browser.wait_for(driver, css: str, timeout: float) -> bool`
  - `browser.create_driver(headless=False, user_data_dir=None)`: raises `ChromeNotFound`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_browser.py`:

```python
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selenium.common.exceptions import NoSuchElementException

import browser


class FindChromeTests(unittest.TestCase):
    def test_finds_macos_app(self):
        with patch("browser.os.path.exists", side_effect=lambda p: p == browser.MAC_CHROME), \
             patch("browser.shutil.which", return_value=None):
            self.assertEqual(browser.find_chrome(), browser.MAC_CHROME)

    def test_finds_windows_local_appdata_install(self):
        local = os.path.join("C:", "Users", "sam", "AppData", "Local")
        expected = os.path.join(local, "Google", "Chrome", "Application", "chrome.exe")
        env = {"LOCALAPPDATA": local, "PROGRAMFILES": "", "PROGRAMFILES(X86)": ""}
        with patch.dict(os.environ, env), \
             patch("browser.os.path.exists", side_effect=lambda p: p == expected), \
             patch("browser.shutil.which", return_value=None):
            self.assertEqual(browser.find_chrome(), expected)

    def test_falls_back_to_path(self):
        def which(name):
            return "/usr/bin/google-chrome-stable" if name == "google-chrome-stable" else None
        with patch("browser.os.path.exists", side_effect=lambda p: p == "/usr/bin/google-chrome-stable"), \
             patch("browser.shutil.which", side_effect=which):
            self.assertEqual(browser.find_chrome(), "/usr/bin/google-chrome-stable")

    def test_returns_none_when_missing(self):
        with patch("browser.os.path.exists", return_value=False), \
             patch("browser.shutil.which", return_value=None):
            self.assertIsNone(browser.find_chrome())


class CreateDriverTests(unittest.TestCase):
    def test_raises_chrome_not_found_without_starting_chrome(self):
        with patch("browser.find_chrome", return_value=None), \
             patch("browser.uc.Chrome") as chrome:
            with self.assertRaises(browser.ChromeNotFound):
                browser.create_driver()
            chrome.assert_not_called()

    def test_passes_the_found_binary(self):
        with patch("browser.find_chrome", return_value="/opt/chrome"), \
             patch("browser._detect_chrome_major", return_value=130), \
             patch("browser.uc.Chrome") as chrome:
            browser.create_driver()
        kwargs = chrome.call_args.kwargs
        self.assertEqual(kwargs["browser_executable_path"], "/opt/chrome")
        self.assertEqual(kwargs["version_main"], 130)


class DetectVersionTests(unittest.TestCase):
    def test_env_override(self):
        with patch.dict(os.environ, {"HAWKEYE_CHROME_MAJOR": "123"}):
            self.assertEqual(browser._detect_chrome_major("/whatever"), 123)


class FakeDriver:
    def __init__(self, appear_after_calls=None):
        self.calls = 0
        self.appear_after_calls = appear_after_calls

    def find_element(self, by, value):
        self.calls += 1
        if self.appear_after_calls is None or self.calls < self.appear_after_calls:
            raise NoSuchElementException("not yet")
        return object()


class WaitForTests(unittest.TestCase):
    def test_true_when_element_appears(self):
        self.assertTrue(browser.wait_for(FakeDriver(appear_after_calls=2), "li", timeout=2))

    def test_false_on_timeout(self):
        self.assertFalse(browser.wait_for(FakeDriver(appear_after_calls=None), "li", timeout=0.3))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_browser -v`
Expected: ERROR / FAIL (`AttributeError: module 'browser' has no attribute 'MAC_CHROME'`, etc.).

- [ ] **Step 3: Rewrite `src/browser.py`**

```python
"""
browser.py — Shared Chrome driver factory
==========================================
Creates the undetected-chromedriver (Selenium) instance shared by the browser
scrapers: bayt.py, naukrigulf.py, google_jobs.py, linkedin_posts.py.

Why undetected-chromedriver?
    Regular Selenium is detected and blocked by Cloudflare (used on Bayt.com).
    undetected-chromedriver patches the Chrome binary at runtime to remove
    automation fingerprints.

Why headless=False?
    Bayt.com's Cloudflare protection specifically blocks headless Chrome.
    Running with a visible window passes the bot detection challenge.

Chrome discovery and version:
    find_chrome() looks in the standard macOS / Windows install folders, then on
    PATH. The Chrome major version is detected at runtime (Windows: registry,
    elsewhere: `chrome --version`) so ChromeDriver always matches; there is no
    hardcoded version. Override with HAWKEYE_CHROME_MAJOR if detection is wrong.
    create_driver() raises ChromeNotFound when Chrome is not installed.

wait_for():
    Scrapers wait until a job-card selector appears instead of sleeping a fixed
    number of seconds.
"""

import os
import re
import shutil
import subprocess
import sys

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_PATH_NAMES = ("google-chrome", "google-chrome-stable", "chrome")


class ChromeNotFound(RuntimeError):
    """Google Chrome is not installed (or not in any place we look)."""


def _windows_candidates() -> list:
    paths = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            paths.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    return paths


def find_chrome():
    """Return the path to the installed Google Chrome binary, or None."""
    candidates = [MAC_CHROME, *_windows_candidates()]
    candidates += [shutil.which(name) for name in _PATH_NAMES]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _windows_chrome_major():
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Google\Chrome\BLBeacon") as key:
                version, _ = winreg.QueryValueEx(key, "version")
        except OSError:
            continue
        match = re.match(r"(\d+)\.", str(version))
        if match:
            return int(match.group(1))
    return None


def _detect_chrome_major(chrome_path=None):
    """
    Installed Chrome major version, so ChromeDriver matches it. None lets
    undetected-chromedriver resolve the version itself.
    """
    override = os.environ.get("HAWKEYE_CHROME_MAJOR")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    if sys.platform == "win32":
        return _windows_chrome_major()  # chrome.exe --version prints nothing on Windows
    path = chrome_path or find_chrome()
    if not path:
        return None
    try:
        out = subprocess.check_output([path, "--version"], text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    match = re.search(r"(\d+)\.\d+\.\d+", out)
    return int(match.group(1)) if match else None


def wait_for(driver, css: str, timeout: float) -> bool:
    """Wait until an element matching `css` exists. False on timeout — the caller parses whatever loaded."""
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.25).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css))
        )
        return True
    except TimeoutException:
        return False


def create_driver(headless=False, user_data_dir=None):
    """
    Create and return a configured undetected-chromedriver instance.

    Args:
        headless (bool): Run Chrome with no visible window.
                         Default False — Bayt.com blocks headless mode via Cloudflare.
        user_data_dir (str|None): Persistent Chrome profile directory, so a
                         logged-in LinkedIn session survives between runs
                         (see paths.linkedin_profile_dir()).

    Chrome options explained:
        --no-sandbox                       : required in some Linux/CI environments
        --disable-dev-shm-usage            : prevents crash in low-memory environments
        --window-size=1920,1080            : ensures a full-size viewport for JS rendering
        --lang=en-US                       : forces English UI so selectors stay consistent
        --disable-blink-features=AutomationControlled : removes the "Chrome is controlled by
                                            automated software" flag from the browser

    Raises:
        ChromeNotFound: Google Chrome is not installed.
    """
    chrome = find_chrome()
    if not chrome:
        raise ChromeNotFound("Google Chrome is not installed")

    opts = uc.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if user_data_dir:
        user_data_dir = os.path.expanduser(str(user_data_dir))
        os.makedirs(user_data_dir, exist_ok=True)
        opts.add_argument(f"--user-data-dir={user_data_dir}")

    driver = uc.Chrome(
        options=opts,
        browser_executable_path=chrome,
        version_main=_detect_chrome_major(chrome),
    )
    driver.set_page_load_timeout(30)
    return driver
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/browser.py tests/test_browser.py
git commit -m "Find Chrome on every OS, add wait_for and ChromeNotFound"
```

---

### Task 6: Single-query JobSpy fetchers (`scraper.py`)

**Files:**
- Modify: `src/scraper.py` (whole file)
- Create: `tests/test_scraper.py`

**Interfaces:**
- Produces (used by Task 9):
  - `scraper.fetch_linkedin(args: dict, seen_urls: set, keywords: list) -> list[dict]`, where `args = {"term", "location"}`.
  - `scraper.fetch_indeed(args: dict, seen_urls: set, keywords: list) -> list[dict]`, where `args = {"term", "location", "country"}`.
  - Both raise on any error.
- Removes: `scraper.scrape_all`. `main.py` breaks until Task 10; this is expected.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scraper.py`:

```python
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import scraper

ROWS = [
    {"job_url": "https://www.linkedin.com/jobs/view/1?utm_source=x", "title": "Java Developer",
     "date_posted": None, "company": "Acme", "location": "Amman, JO", "site": "linkedin"},
    {"job_url": "https://www.linkedin.com/jobs/view/2", "title": "Chef",
     "date_posted": None, "company": "Hotel", "location": "Amman", "site": "linkedin"},
    {"job_url": "https://www.linkedin.com/jobs/view/3", "title": "Java Engineer",
     "date_posted": "2020-01-01", "company": "Old", "location": "Amman", "site": "linkedin"},
]


class FetchLinkedInTests(unittest.TestCase):
    def test_parses_filters_and_passes_args(self):
        with patch("scraper.scrape_jobs", return_value=pd.DataFrame(ROWS)) as scrape_jobs:
            jobs = scraper.fetch_linkedin({"term": "Java", "location": "Jordan"}, set(), ["Java Developer"])
        self.assertEqual([j["url"] for j in jobs], ["https://www.linkedin.com/jobs/view/1"])
        self.assertEqual(jobs[0]["description"], "Acme - Amman, JO")
        self.assertEqual(jobs[0]["source"], "Linkedin")
        self.assertEqual(jobs[0]["location"], "Jordan")
        kwargs = scrape_jobs.call_args.kwargs
        self.assertEqual(kwargs["site_name"], ["linkedin"])
        self.assertEqual((kwargs["search_term"], kwargs["location"]), ("Java", "Jordan"))
        self.assertEqual((kwargs["results_wanted"], kwargs["hours_old"]), (10, 48))

    def test_skips_urls_already_seen_in_this_lane(self):
        seen = {"https://www.linkedin.com/jobs/view/1"}
        with patch("scraper.scrape_jobs", return_value=pd.DataFrame(ROWS)):
            self.assertEqual(scraper.fetch_linkedin({"term": "Java", "location": "Jordan"}, seen, ["Java"]), [])

    def test_errors_propagate(self):
        with patch("scraper.scrape_jobs", side_effect=RuntimeError("429 Too Many Requests")):
            with self.assertRaises(RuntimeError):
                scraper.fetch_linkedin({"term": "Java", "location": "Jordan"}, set(), ["Java"])


class FetchIndeedTests(unittest.TestCase):
    def test_passes_country(self):
        with patch("scraper.scrape_jobs", return_value=pd.DataFrame([])) as scrape_jobs:
            jobs = scraper.fetch_indeed(
                {"term": "Java", "location": "United Arab Emirates", "country": "united arab emirates"},
                set(), ["Java"],
            )
        self.assertEqual(jobs, [])
        kwargs = scrape_jobs.call_args.kwargs
        self.assertEqual(kwargs["site_name"], ["indeed"])
        self.assertEqual(kwargs["country_indeed"], "united arab emirates")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_scraper -v`
Expected: ERROR `AttributeError: module 'scraper' has no attribute 'fetch_linkedin'`.

- [ ] **Step 3: Rewrite `src/scraper.py`**

```python
"""
scraper.py — LinkedIn + Indeed via JobSpy
==========================================
Uses the python-jobspy library (no browser required). Each fetch_* function runs
ONE search and raises on any error; pipeline.py loops over the queries and
decides what to do with failures.

Titles are kept only if they match the user's keywords (is_relevant_job) and
were posted within 48 hours (is_within_48h).
"""

import logging
from datetime import datetime

from jobspy import scrape_jobs

from filters import is_relevant_job
from utils import clean_url, is_within_48h

log = logging.getLogger(__name__)

RESULTS_WANTED = 10
HOURS_OLD = 48


def _parse_jobs(jobs, seen_urls, location, keywords):
    """Convert a JobSpy DataFrame into standardised job dicts."""
    result = []
    skipped = 0
    for _, row in jobs.iterrows():
        url = clean_url(str(row.get("job_url", "")).strip())
        if not url or url in seen_urls:
            continue
        title = str(row.get("title", "")).strip()
        if not is_relevant_job(title, keywords):
            skipped += 1
            continue
        date_p = str(row.get("date_posted", "")).strip()
        if not is_within_48h(date_p):
            skipped += 1
            continue
        seen_urls.add(url)
        company = str(row.get("company", "")).strip()
        loc = str(row.get("location", "")).strip()
        source = str(row.get("site", "")).strip().capitalize()
        result.append({
            "title": title,
            "url": url,
            "description": f"{company} - {loc}" if company else loc,
            "source": source,
            "keyword": "",
            "location": location,
            "published": date_p,
            "date_found": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        })
    if skipped:
        log.debug("Filtered %d irrelevant/old jobs for %s", skipped, location)
    return result


def fetch_linkedin(args: dict, seen_urls: set, keywords: list) -> list:
    """One LinkedIn Jobs search. args = {"term", "location"}."""
    jobs = scrape_jobs(
        site_name=["linkedin"],
        search_term=args["term"],
        location=args["location"],
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
    )
    return _parse_jobs(jobs, seen_urls, args["location"], keywords)


def fetch_indeed(args: dict, seen_urls: set, keywords: list) -> list:
    """One Indeed search. args = {"term", "location", "country"}."""
    jobs = scrape_jobs(
        site_name=["indeed"],
        search_term=args["term"],
        location=args["location"],
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        country_indeed=args["country"],
    )
    return _parse_jobs(jobs, seen_urls, args["location"], keywords)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/scraper.py tests/test_scraper.py
git commit -m "Split JobSpy scraping into single-query fetchers that raise"
```

---

### Task 7: Bayt and NaukriGulf single-query fetchers

**Files:**
- Modify: `src/bayt.py` (imports, the `scrape_bayt` function → `fetch_bayt`, module docstring "Cloudflare" line)
- Modify: `src/naukrigulf.py` (imports, `scrape_naukrigulf` → `fetch_naukrigulf`)
- Create: `tests/fixtures/bayt.html`, `tests/fixtures/naukrigulf.html`, `tests/test_browser_scrapers.py`

**Interfaces:**
- Consumes: `browser.wait_for(driver, css, timeout) -> bool` (Task 5).
- Produces (used by Task 9):
  - `bayt.fetch_bayt(driver, args: tuple[str, str], seen_urls: set, keywords: list) -> list[dict]`, where `args = (term_slug, country_slug)`.
  - `naukrigulf.fetch_naukrigulf(driver, args: tuple[str, str], seen_urls: set, keywords: list) -> list[dict]`, where `args = (keyword, location_slug)`.
  - Both raise on navigation/driver errors.

**About the fixtures:** they are hand-written, minimal pages that use exactly the selectors the parsers read. They are not saved copies of the live sites. They prove the parsing and filtering logic. Real-site breakage is caught by the manual run in Task 11 and by the daily canary planned for sub-project 3.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/bayt.html`:

```html
<!doctype html>
<html><head><title>Java Developer Jobs in Jordan | Bayt.com</title></head>
<body>
<ul>
  <li data-job-id="101">
    <h2 class="jb-title"><a href="/en/jordan/jobs/senior-java-developer-101/">Senior Java Developer</a></h2>
    <a class="t-default t-bold" href="/en/company/acme-software/">Acme Software</a>
    <div class="job-company-location-wrapper"><div class="t-mute"><span>Amman, Jordan</span></div></div>
    <span data-automation-id="job-active-date">Yesterday</span>
  </li>
  <li data-job-id="102">
    <h2 class="jb-title"><a href="/en/jordan/jobs/accountant-102/">Accountant</a></h2>
    <a class="t-default t-bold" href="/en/company/numbers/">Numbers Co</a>
    <div class="job-company-location-wrapper"><div class="t-mute"><span>Irbid, Jordan</span></div></div>
    <span data-automation-id="job-active-date">Today</span>
  </li>
  <li data-job-id="103">
    <h2 class="jb-title"><a href="/en/jordan/jobs/java-spring-boot-engineer-103/">Java Spring Boot Engineer</a></h2>
    <a class="t-default t-bold" href="/en/company/old-corp/">Old Corp</a>
    <div class="job-company-location-wrapper"><div class="t-mute"><span>Amman, Jordan</span></div></div>
    <span data-automation-id="job-active-date">30+ days ago</span>
  </li>
</ul>
</body></html>
```

`tests/fixtures/naukrigulf.html`:

```html
<!doctype html>
<html><head><title>Java Developer Jobs in Saudi Arabia - Naukrigulf.com</title></head>
<body>
  <div class="ng-box srp-tuple">
    <a class="info-position" href="https://www.naukrigulf.com/java-developer-jobs-in-riyadh-saudi-arabia-in-acme-gulf-5-to-8-years-n-cd-111"><p class="designation-title">Java Developer</p></a>
    <a class="info-org" href="https://www.naukrigulf.com/acme-gulf-jobs">Acme Gulf</a>
    <ul><li class="info-loc"><span class="ico-loc"></span><span>Riyadh - Saudi Arabia</span></li></ul>
    <span class="time">1 day ago</span>
  </div>
  <div class="ng-box srp-tuple">
    <a class="info-position" href="/sales-manager-jobs-in-jeddah-saudi-arabia-n-cd-112"><p class="designation-title">Sales Manager</p></a>
    <a class="info-org" href="#">Retail Group</a>
    <ul><li class="info-loc"><span class="ico-loc"></span><span>Jeddah - Saudi Arabia</span></li></ul>
    <span class="time">Today</span>
  </div>
  <div class="ng-box srp-tuple">
    <a class="info-position" href="/java-architect-jobs-in-riyadh-saudi-arabia-n-cd-113"><p class="designation-title">Java Architect</p></a>
    <a class="info-org" href="#">Old Corp</a>
    <ul><li class="info-loc"><span class="ico-loc"></span><span>Riyadh - Saudi Arabia</span></li></ul>
    <span class="time">3 weeks ago</span>
  </div>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_browser_scrapers.py`:

```python
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import bayt
import naukrigulf

FIXTURES = Path(__file__).resolve().parent / "fixtures"
KEYWORDS = ["Java Developer"]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeDriver:
    """Just enough of a Selenium driver for the fetch_* functions."""

    def __init__(self, html="", title="Page", current_url="https://example.com/", fail_on_get=None):
        self.page_source = html
        self.title = title
        self.current_url = current_url
        self.visited = []
        self.scripts = []
        self.window_handles = ["main"]
        self.switch_to = SimpleNamespace(window=lambda handle: None)
        self.fail_on_get = fail_on_get

    def get(self, url):
        if self.fail_on_get:
            raise self.fail_on_get
        self.visited.append(url)

    def close(self):
        pass

    def execute_script(self, script):
        self.scripts.append(script)


class BaytTests(unittest.TestCase):
    def test_fetch_parses_relevant_recent_jobs(self):
        driver = FakeDriver(fixture("bayt.html"))
        with patch("bayt.wait_for", return_value=True) as wait:
            jobs = bayt.fetch_bayt(driver, ("java-developer", "jordan"), set(), KEYWORDS)
        self.assertEqual(driver.visited, ["https://www.bayt.com/en/jordan/jobs/java-developer-jobs/"])
        wait.assert_called_once_with(driver, "li[data-job-id]", 20)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["title"], "Senior Java Developer")
        self.assertEqual(job["url"], "https://www.bayt.com/en/jordan/jobs/senior-java-developer-101/")
        self.assertEqual(job["description"], "Acme Software - Amman, Jordan")
        self.assertEqual((job["source"], job["location"], job["published"]), ("Bayt", "jordan", "Yesterday"))

    def test_warns_when_nothing_loaded(self):
        driver = FakeDriver("<html></html>", title="Just a moment...")
        with patch("bayt.wait_for", return_value=False), self.assertLogs("bayt", "WARNING") as logs:
            self.assertEqual(bayt.fetch_bayt(driver, ("java", "jordan"), set(), KEYWORDS), [])
        self.assertIn("Just a moment", logs.output[0])

    def test_navigation_errors_propagate(self):
        driver = FakeDriver(fail_on_get=RuntimeError("net::ERR_TIMED_OUT"))
        with patch("bayt.wait_for", return_value=True):
            with self.assertRaises(RuntimeError):
                bayt.fetch_bayt(driver, ("java", "jordan"), set(), KEYWORDS)


class NaukriGulfTests(unittest.TestCase):
    def test_fetch_parses_relevant_recent_jobs(self):
        driver = FakeDriver(fixture("naukrigulf.html"))
        with patch("naukrigulf.wait_for", return_value=True) as wait:
            jobs = naukrigulf.fetch_naukrigulf(driver, ("java developer", "saudi-arabia"), set(), KEYWORDS)
        self.assertEqual(driver.visited, ["https://www.naukrigulf.com/java-developer-jobs-in-saudi-arabia"])
        wait.assert_called_once_with(driver, ".ng-box.srp-tuple", 15)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["title"], "Java Developer")
        self.assertEqual(job["description"], "Acme Gulf - Riyadh - Saudi Arabia")
        self.assertEqual((job["source"], job["published"]), ("NaukriGulf", "1 day ago"))

    def test_seen_urls_are_skipped(self):
        driver = FakeDriver(fixture("naukrigulf.html"))
        seen = {"https://www.naukrigulf.com/java-developer-jobs-in-riyadh-saudi-arabia-in-acme-gulf-5-to-8-years-n-cd-111"}
        with patch("naukrigulf.wait_for", return_value=True):
            self.assertEqual(naukrigulf.fetch_naukrigulf(driver, ("java developer", "saudi-arabia"), seen, KEYWORDS), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_browser_scrapers -v`
Expected: ERROR `AttributeError: module 'bayt' has no attribute 'fetch_bayt'` (and the same for naukrigulf).

- [ ] **Step 4: Update `src/bayt.py`**

In the module docstring, replace the line `A visible Chrome window (headless=False) passes the JS challenge after ~8 seconds.` with:

```
    A visible Chrome window (headless=False) passes the JS challenge; fetch_bayt()
    waits up to BAYT_WAIT_SECONDS for job cards to appear.
```

Replace the imports with:

```python
import logging
from datetime import datetime

from bs4 import BeautifulSoup

from browser import wait_for
from filters import is_relevant_job
from utils import is_within_48h

log = logging.getLogger(__name__)

BAYT_WAIT_SECONDS = 20  # Cloudflare challenge + render
CARD_SELECTOR = "li[data-job-id]"
```

Replace the entire `scrape_bayt` function with:

```python
def _build_url(term_slug: str, country_slug: str) -> str:
    return f"https://www.bayt.com/en/{country_slug}/jobs/{term_slug}-jobs/"


def fetch_bayt(driver, args, seen_urls: set, keywords: list) -> list:
    """One Bayt search. args = (term_slug, country_slug). Raises on navigation errors."""
    term_slug, country_slug = args
    url = _build_url(term_slug, country_slug)
    _ensure_window(driver)
    driver.get(url)
    loaded = wait_for(driver, CARD_SELECTOR, BAYT_WAIT_SECONDS)
    _ensure_window(driver)
    jobs = _parse_bayt_page(driver.page_source, seen_urls, country_slug, keywords)
    if not jobs and not loaded:
        log.warning("Bayt: no job cards after %ss at %s (page title: %r)", BAYT_WAIT_SECONDS, url, driver.title)
    return jobs
```

- [ ] **Step 5: Update `src/naukrigulf.py`**

Replace the imports with:

```python
import logging
from datetime import datetime

from bs4 import BeautifulSoup

from browser import wait_for
from filters import is_relevant_job
from utils import is_within_48h

log = logging.getLogger(__name__)

NAUKRI_WAIT_SECONDS = 15  # Angular SPA render
CARD_SELECTOR = ".ng-box.srp-tuple"
```

(`BASE = "https://www.naukrigulf.com"` stays right below.)

Replace the entire `scrape_naukrigulf` function with:

```python
def fetch_naukrigulf(driver, args, seen_urls: set, keywords: list) -> list:
    """One NaukriGulf search. args = (keyword, location_slug). Raises on navigation errors."""
    keyword, location_slug = args
    url = _build_url(keyword, location_slug)
    _ensure_main_window(driver)
    driver.get(url)
    loaded = wait_for(driver, CARD_SELECTOR, NAUKRI_WAIT_SECONDS)
    _ensure_main_window(driver)
    jobs = _parse_naukrigulf_page(driver.page_source, seen_urls, location_slug, keywords)
    if not jobs and not loaded:
        log.warning("NaukriGulf: no job cards after %ss at %s (page title: %r)", NAUKRI_WAIT_SECONDS, url, driver.title)
    return jobs
```

The naukrigulf module docstring needs no change. The old `time.sleep(6)` note lives only in ARCHITECTURE.md and is updated in Task 11.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bayt.py src/naukrigulf.py tests/fixtures/bayt.html tests/fixtures/naukrigulf.html tests/test_browser_scrapers.py
git commit -m "Bayt and NaukriGulf: single-query fetchers with smart waits"
```

---

### Task 8: Google Jobs and LinkedIn posts single-query fetchers

**Files:**
- Modify: `src/google_jobs.py` (imports, `scrape_google_jobs` → `fetch_google_jobs`)
- Modify: `src/linkedin_posts.py` (docstring "Authentication" paragraph, imports, new exception, `scrape_linkedin_posts` → `fetch_linkedin_posts`)
- Create: `tests/fixtures/google_jobs.html`, `tests/fixtures/linkedin_posts.html`
- Modify: `tests/test_browser_scrapers.py`

**Interfaces:**
- Consumes: `browser.wait_for` (Task 5); `FakeDriver` / `fixture()` / `KEYWORDS` in `tests/test_browser_scrapers.py` (Task 7).
- Produces (used by Task 9):
  - `google_jobs.fetch_google_jobs(driver, args: tuple[str, str], seen_urls, keywords) -> list[dict]`, where `args = (term, location)`.
  - `linkedin_posts.fetch_linkedin_posts(driver, args: str, seen_urls, keywords) -> list[dict]`, where `args` is a phrase like `"hiring Java"`.
  - `linkedin_posts.LinkedInLoginRequired(RuntimeError)`.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/google_jobs.html`:

```html
<!doctype html>
<html><head><title>java developer jobs UAE - Google Search</title></head>
<body>
  <div class="PUpOsf">Java Developer</div>
  <div class="a3jPc">Acme Cloud</div>
  <div class="FqK3wc">Dubai • via LinkedIn</div>
  <div role="list" class="EDblX">
    <a href="https://www.google.com/search?q=acme">Google</a>
    <a href="https://ae.linkedin.com/jobs/view/java-developer-at-acme-cloud-123">LinkedIn</a>
    <a href="https://www.bayt.com/en/uae/jobs/java-developer-555/">Bayt</a>
  </div>
  <div class="PUpOsf">Head Chef</div>
  <div class="a3jPc">Palm Hotel</div>
  <div class="FqK3wc">Abu Dhabi</div>
  <div role="list" class="EDblX">
    <a href="https://careers.example.com/head-chef">Palm Hotel careers</a>
  </div>
</body></html>
```

`tests/fixtures/linkedin_posts.html`:

```html
<!doctype html>
<!-- Synthetic, anonymised fixture: names and text are invented; only the DOM structure mirrors LinkedIn. -->
<html><head><title>Search | LinkedIn</title></head>
<body>
  <div data-urn="urn:li:activity:7300000000000000001">
    <span class="update-components-actor__title">Sample Recruiter</span>
    <span class="update-components-actor__sub-description">5h • Edited</span>
    <div class="update-components-text">We're hiring a Java Developer in Amman! Send your CV to the email in the comments.</div>
  </div>
  <div data-urn="urn:li:activity:7300000000000000002">
    <span class="update-components-actor__title">Sample Learner</span>
    <span class="update-components-actor__sub-description">2h</span>
    <div class="update-components-text">Just finished a great Java course, happy weekend everyone.</div>
  </div>
  <div data-urn="urn:li:activity:7300000000000000003">
    <span class="update-components-actor__title">Sample Founder</span>
    <span class="update-components-actor__sub-description">3w</span>
    <div class="update-components-text">Hiring Java engineers for our team, apply now.</div>
  </div>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_browser_scrapers.py`, add `import google_jobs` and `import linkedin_posts` next to the other module imports. Add these classes above `if __name__ == "__main__":`:

```python
class GoogleJobsTests(unittest.TestCase):
    def test_fetch_takes_first_non_google_apply_link(self):
        driver = FakeDriver(fixture("google_jobs.html"))
        with patch("google_jobs.wait_for", return_value=True) as wait:
            jobs = google_jobs.fetch_google_jobs(driver, ("Java Developer", "UAE"), set(), KEYWORDS)
        self.assertEqual(driver.visited, ["https://www.google.com/search?q=Java+Developer+jobs+UAE&ibp=htl;jobs&hl=en"])
        wait.assert_called_once_with(driver, ".PUpOsf", 15)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["url"], "https://ae.linkedin.com/jobs/view/java-developer-at-acme-cloud-123")
        self.assertEqual(job["description"], "Acme Cloud - Dubai")
        self.assertEqual((job["source"], job["location"], job["published"]), ("Google Jobs", "UAE", ""))

    def test_closes_extra_tabs(self):
        driver = FakeDriver(fixture("google_jobs.html"))
        driver.window_handles = ["main", "popup"]
        switched = []
        driver.switch_to = SimpleNamespace(window=switched.append)
        with patch("google_jobs.wait_for", return_value=True):
            google_jobs.fetch_google_jobs(driver, ("Java Developer", "UAE"), set(), KEYWORDS)
        self.assertEqual(switched, ["popup", "main"])


class LinkedInPostsTests(unittest.TestCase):
    def test_fetch_keeps_recent_hiring_posts(self):
        driver = FakeDriver(fixture("linkedin_posts.html"), current_url="https://www.linkedin.com/search/results/content/")
        with patch("linkedin_posts.wait_for", return_value=True) as wait, \
             patch("linkedin_posts.time.sleep"):
            jobs = linkedin_posts.fetch_linkedin_posts(driver, "hiring Java Developer", set(), KEYWORDS)
        wait.assert_called_once_with(driver, 'div[data-urn*="urn:li:activity"]', 15)
        self.assertEqual(len(driver.scripts), linkedin_posts._MAX_SCROLLS)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["url"], "https://www.linkedin.com/feed/update/urn:li:activity:7300000000000000001/")
        self.assertEqual(job["description"], "Sample Recruiter · LinkedIn hiring post")
        self.assertEqual((job["source"], job["published"]), ("LinkedIn Post", "5h"))

    def test_raises_when_logged_out(self):
        driver = FakeDriver("<html></html>", current_url="https://www.linkedin.com/authwall?trk=x")
        with patch("linkedin_posts.wait_for", return_value=False), \
             patch("linkedin_posts.time.sleep"):
            with self.assertRaises(linkedin_posts.LinkedInLoginRequired):
                linkedin_posts.fetch_linkedin_posts(driver, "hiring Java", set(), KEYWORDS)
        self.assertEqual(driver.scripts, [])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_browser_scrapers -v`
Expected: ERROR `AttributeError: module 'google_jobs' has no attribute 'fetch_google_jobs'`, and `'linkedin_posts' has no attribute ...`.

- [ ] **Step 4: Update `src/google_jobs.py`**

Replace the imports with:

```python
import logging
from datetime import datetime

from bs4 import BeautifulSoup

from browser import wait_for
from filters import is_relevant_job

log = logging.getLogger(__name__)

GOOGLE_WAIT_SECONDS = 15
CARD_SELECTOR = ".PUpOsf"
```

(`SKIP_DOMAINS = [...]` stays right below.)

Replace the entire `scrape_google_jobs` function with:

```python
def _close_extra_tabs(driver):
    handles = driver.window_handles
    for handle in handles[1:]:
        driver.switch_to.window(handle)
        driver.close()
    driver.switch_to.window(driver.window_handles[0])


def fetch_google_jobs(driver, args, seen_urls: set, keywords: list) -> list:
    """One Google Jobs search. args = (term, location). Raises on navigation errors."""
    term, location = args
    url = _build_url(term, location)
    driver.get(url)
    loaded = wait_for(driver, CARD_SELECTOR, GOOGLE_WAIT_SECONDS)
    _close_extra_tabs(driver)
    jobs = _parse_google_page(driver.page_source, seen_urls, location, keywords)
    if not jobs and not loaded:
        log.warning("Google Jobs: no job cards after %ss at %s (page title: %r)", GOOGLE_WAIT_SECONDS, url, driver.title)
    return jobs
```

In the `test_closes_extra_tabs` fake, `driver.window_handles` stays `["main", "popup"]` after `close()`, so the final `switch_to.window(driver.window_handles[0])` receives `"main"`. That is what the test expects.

- [ ] **Step 5: Update `src/linkedin_posts.py`**

In the module docstring, replace the "Authentication (REQUIRED):" paragraph with:

```
Authentication (REQUIRED):
    LinkedIn shows an auth wall to anonymous visitors on content search. This
    scraper reuses a persistent Chrome profile (paths.linkedin_profile_dir()).
    Log into LinkedIn ONCE by hand in that profile; the session cookie is then
    reused on later runs. If the session is missing or expired,
    fetch_linkedin_posts() raises LinkedInLoginRequired and the pipeline skips
    the remaining post searches.
```

Replace the imports and constants (from `import os` through `_SCROLL_PAUSE = 2.0 ...`) with:

```python
import re
import time
from datetime import datetime
from urllib.parse import quote

from bs4 import BeautifulSoup

from browser import wait_for
from filters import is_hiring_post

_MAX_SCROLLS = 4          # how many times to scroll to lazy-load more posts
_SCROLL_PAUSE = 2.0       # seconds to wait after each scroll
POSTS_WAIT_SECONDS = 15
CARD_SELECTOR = 'div[data-urn*="urn:li:activity"]'


class LinkedInLoginRequired(RuntimeError):
    """LinkedIn redirected to a login / authwall page: the user must log in once in the Chrome profile."""
```

Replace the entire `scrape_linkedin_posts` function with:

```python
def fetch_linkedin_posts(driver, args, seen_urls: set, keywords: list) -> list:
    """One LinkedIn content search. args = phrase, e.g. "hiring Java Developer". Raises LinkedInLoginRequired."""
    phrase = args
    driver.get(_build_url(phrase))
    if _is_logged_out(driver):
        raise LinkedInLoginRequired(driver.current_url)
    wait_for(driver, CARD_SELECTOR, POSTS_WAIT_SECONDS)
    if _is_logged_out(driver):
        raise LinkedInLoginRequired(driver.current_url)

    for _ in range(_MAX_SCROLLS):
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight);")
        time.sleep(_SCROLL_PAUSE)

    return _parse_posts_page(driver.page_source, seen_urls, phrase, keywords)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/google_jobs.py src/linkedin_posts.py tests/fixtures/google_jobs.html tests/fixtures/linkedin_posts.html tests/test_browser_scrapers.py
git commit -m "Google Jobs and LinkedIn posts: single-query fetchers; LinkedInLoginRequired"
```

---

### Task 9: The pipeline (`pipeline.py`)

**Files:**
- Create: `src/pipeline.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes:
  - `catalog.plan_queries`, `catalog.SOURCE_ORDER`, `catalog.BROWSER_SOURCES` (Task 4).
  - `store.Store` with `start_run`, `add_jobs`, `finish_run` (Task 2).
  - `browser.ChromeNotFound`, `browser.create_driver` (Task 5).
  - `linkedin_posts.LinkedInLoginRequired` (Task 8).
  - The `fetch_*` functions (Tasks 6–8).
  - `paths.linkedin_profile_dir()` (Task 1).
- Produces (used by Task 10):
  - `pipeline.RunSummary(run_id: int, state: str, found: int, new_jobs: list[dict])`.
  - `pipeline.run_search(settings, on_event=None, cancel=None, *, country_labels=None, store=None, fetchers=None, driver_factory=None) -> RunSummary`.
  - `fetchers` maps source → callable:
    - HTTP sources: `(args, seen_urls, keywords)`.
    - Browser sources: `(driver, args, seen_urls, keywords)`.
  - `pipeline.HTTP_SOURCES = ("linkedin", "indeed")`.
- Errors before `run_started` (e.g. the DB cannot be written) propagate. After `run_started`, `run_search` never raises, and a `KeyboardInterrupt` sets `cancel`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from browser import ChromeNotFound
from linkedin_posts import LinkedInLoginRequired
from pipeline import run_search
from store import Store


def job_for(source, args, url=None):
    return {
        "title": f"Java Developer ({source})",
        "url": url or f"https://example.com/{source}/{quote(str(args), safe='')}",
        "description": "Acme - Amman",
        "source": source,
        "location": "x",
        "published": "",
    }


class FakeDriver:
    def __init__(self):
        self.alive = True
        self.quit_called = False

    @property
    def window_handles(self):
        if not self.alive:
            raise RuntimeError("invalid session id")
        return ["main"]

    def quit(self):
        self.quit_called = True


class Recorder:
    """Fake fetchers + driver factory that record what ran."""

    def __init__(self):
        self.calls = []
        self.drivers = []
        self.lock = threading.Lock()
        self.behaviour = {}  # source -> callable(driver_or_None, args) that may raise / return jobs

    def http(self, source):
        def fetch(args, seen_urls, keywords):
            with self.lock:
                self.calls.append((source, args))
            if source in self.behaviour:
                return self.behaviour[source](None, args)
            return [job_for(source, args)]
        return fetch

    def browser(self, source):
        def fetch(driver, args, seen_urls, keywords):
            with self.lock:
                self.calls.append((source, args))
            if source in self.behaviour:
                return self.behaviour[source](driver, args)
            return [job_for(source, args)]
        return fetch

    def fetchers(self):
        result = {s: self.http(s) for s in ("linkedin", "indeed")}
        result.update({s: self.browser(s) for s in ("bayt", "naukrigulf", "google_jobs", "linkedin_posts")})
        return result

    def driver_factory(self):
        driver = FakeDriver()
        self.drivers.append(driver)
        return driver


def settings(keywords=("Java",), locations=("jo",), sources=("linkedin", "bayt")):
    return {"keywords": list(keywords), "locations": list(locations), "sources": list(sources)}


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")
        self.rec = Recorder()
        self.events = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def run_search(self, cfg, **kwargs):
        kwargs.setdefault("store", self.store)
        kwargs.setdefault("fetchers", self.rec.fetchers())
        kwargs.setdefault("driver_factory", self.rec.driver_factory)
        kwargs.setdefault("on_event", self.events.append)
        return run_search(cfg, **kwargs)

    def types(self):
        return [e["type"] for e in self.events]

    def of_type(self, kind):
        return [e for e in self.events if e["type"] == kind]


class HappyPathTests(PipelineTestCase):
    def test_events_order_and_totals(self):
        summary = self.run_search(settings())
        self.assertEqual(self.events[0], {"type": "run_started", "run_id": summary.run_id, "total": 2})
        self.assertEqual(self.events[-1], {"type": "run_finished", "run_id": summary.run_id,
                                           "state": "done", "found": 2, "new": 2})
        for done in self.of_type("query_done"):
            started_at = self.events.index({k: done[k] for k in ("index", "source", "keyword", "location_id")}
                                           | {"type": "query_started"})
            self.assertLess(started_at, self.events.index(done))
        self.assertEqual((summary.state, summary.found, len(summary.new_jobs)), ("done", 2, 2))
        rows = self.store.list_jobs()
        self.assertEqual({(r["keyword"], r["location_id"]) for r in rows}, {("Java", "jo")})
        self.assertTrue(self.rec.drivers[0].quit_called)

    def test_second_run_reports_no_new_jobs(self):
        self.run_search(settings())
        self.events.clear()
        summary = self.run_search(settings())
        self.assertEqual((summary.found, summary.new_jobs), (2, []))
        self.assertEqual(self.events[-1]["new"], 0)

    def test_browser_lane_runs_sources_in_order(self):
        self.run_search(settings(sources=("linkedin_posts", "google_jobs", "bayt")))
        order = [source for source, _ in self.rec.calls]
        self.assertEqual(order, ["bayt", "google_jobs", "linkedin_posts", "linkedin_posts"])

    def test_no_driver_without_browser_sources(self):
        summary = self.run_search(settings(sources=("linkedin",)))
        self.assertEqual(summary.state, "done")
        self.assertEqual(self.rec.drivers, [])

    def test_no_queries_finishes_immediately(self):
        summary = self.run_search(settings(sources=("naukrigulf",)))  # NaukriGulf has no Jordan
        self.assertEqual(self.types(), ["run_started", "run_finished"])
        self.assertEqual(self.events[0]["total"], 0)
        self.assertEqual(summary.state, "done")

    def test_jobs_saved_per_query(self):
        counts = []

        def on_event(event):
            if event["type"] == "query_done":
                counts.append(len(self.store.list_jobs()))

        self.run_search(settings(keywords=("Java", "Kotlin"), sources=("linkedin",)), on_event=on_event)
        self.assertEqual(counts, [1, 2])

    def test_same_url_from_both_lanes_is_new_once(self):
        shared = "https://jobs.example.com/same"
        self.rec.behaviour["linkedin"] = lambda d, a: [job_for("linkedin", a, url=shared)]
        self.rec.behaviour["bayt"] = lambda d, a: [job_for("bayt", a, url=shared)]
        summary = self.run_search(settings())
        self.assertEqual(summary.found, 2)
        self.assertEqual(len(summary.new_jobs), 1)

    def test_on_event_exception_is_ignored(self):
        def broken(event):
            raise ValueError("UI bug")
        summary = self.run_search(settings(), on_event=broken)
        self.assertEqual(summary.state, "done")


class FailureTests(PipelineTestCase):
    def test_failing_query_does_not_stop_run(self):
        def boom(driver, args):
            raise ValueError("bad page")
        self.rec.behaviour["linkedin"] = boom
        summary = self.run_search(settings())
        failed = self.of_type("query_failed")
        self.assertEqual([(e["source"], e["error"]) for e in failed], [("linkedin", "ValueError")])
        self.assertEqual([e["source"] for e in self.of_type("query_done")], ["bayt"])
        self.assertEqual(summary.state, "done")

    def test_login_required_skips_remaining_posts(self):
        def logged_out(driver, args):
            raise LinkedInLoginRequired("https://www.linkedin.com/authwall")
        self.rec.behaviour["linkedin_posts"] = logged_out
        summary = self.run_search(settings(sources=("linkedin_posts",)))
        self.assertEqual(len(self.rec.calls), 1)
        self.assertEqual(self.of_type("login_required"), [{"type": "login_required", "source": "linkedin_posts"}])
        self.assertEqual(self.of_type("query_failed")[0]["error"], "LinkedInLoginRequired")
        skipped = self.of_type("query_skipped")
        self.assertEqual([(e["index"], e["reason"]) for e in skipped], [(1, "login_required")])
        self.assertEqual(summary.state, "done")

    def test_chrome_not_found_skips_browser_but_http_completes(self):
        def no_chrome():
            raise ChromeNotFound("Google Chrome is not installed")
        summary = self.run_search(settings(), driver_factory=no_chrome)
        self.assertEqual(self.of_type("browser_failed"), [{"type": "browser_failed", "reason": "chrome_not_found"}])
        self.assertEqual([(e["source"], e["reason"]) for e in self.of_type("query_skipped")],
                         [("bayt", "browser_unavailable")])
        self.assertEqual([e["source"] for e in self.of_type("query_done")], ["linkedin"])
        self.assertEqual(summary.state, "done")

    def test_start_failure_reason(self):
        def broken():
            raise RuntimeError("session not created")
        self.run_search(settings(sources=("bayt",)), driver_factory=broken)
        self.assertEqual(self.of_type("browser_failed")[0]["reason"], "start_failed")

    def test_single_crash_restarts_driver(self):
        crashed = []

        def crash_once(driver, args):
            if not crashed:
                crashed.append(args)
                driver.alive = False
                raise RuntimeError("invalid session id")
            return [job_for("bayt", args)]

        self.rec.behaviour["bayt"] = crash_once
        summary = self.run_search(settings(keywords=("Java", "Kotlin"), sources=("bayt",)))
        self.assertEqual(len(self.rec.drivers), 2)
        self.assertTrue(all(d.quit_called for d in self.rec.drivers))
        self.assertEqual([e["index"] for e in self.of_type("query_failed")], [0])
        self.assertEqual([e["index"] for e in self.of_type("query_done")], [1])
        self.assertEqual(self.of_type("browser_failed"), [])
        self.assertEqual(summary.state, "done")

    def test_second_crash_skips_rest(self):
        def always_crash(driver, args):
            driver.alive = False
            raise RuntimeError("invalid session id")

        self.rec.behaviour["bayt"] = always_crash
        self.run_search(settings(keywords=("Java", "Kotlin", "Go"), sources=("bayt",)))
        self.assertEqual([e["index"] for e in self.of_type("query_failed")], [0, 1])
        self.assertEqual(self.of_type("browser_failed"), [{"type": "browser_failed", "reason": "crashed"}])
        self.assertEqual([(e["index"], e["reason"]) for e in self.of_type("query_skipped")],
                         [(2, "browser_unavailable")])

    def test_driver_quit_after_failing_query(self):
        def boom(driver, args):
            raise ValueError("bad page")
        self.rec.behaviour["bayt"] = boom
        self.run_search(settings(sources=("bayt",)))
        self.assertEqual(len(self.rec.drivers), 1)
        self.assertTrue(self.rec.drivers[0].quit_called)

    def test_store_failure_marks_run_failed(self):
        class BrokenStore(Store):
            def add_jobs(self, run_id, jobs):
                raise sqlite3.OperationalError("database is locked")

        broken = BrokenStore(Path(self.tmp.name) / "broken.db")
        try:
            summary = self.run_search(settings(), store=broken)
            self.assertEqual(summary.state, "failed")
            self.assertEqual(self.events[-1]["state"], "failed")
            self.assertEqual(broken.list_runs()[0]["state"], "failed")
        finally:
            broken.close()


class CancelTests(PipelineTestCase):
    def test_cancel_stops_after_in_flight_query(self):
        cancel = threading.Event()

        def cancel_during_first(driver, args):
            cancel.set()
            return [job_for("linkedin", args)]

        self.rec.behaviour["linkedin"] = cancel_during_first
        summary = self.run_search(settings(keywords=("Java", "Kotlin", "Go"), sources=("linkedin",)), cancel=cancel)
        self.assertEqual(len(self.rec.calls), 1)
        self.assertEqual(summary.state, "cancelled")
        self.assertEqual(len(summary.new_jobs), 1)
        self.assertEqual(self.events[-1]["state"], "cancelled")
        self.assertEqual(self.store.list_runs()[0]["state"], "cancelled")

    def test_ctrl_c_in_caller_cancels_run(self):
        def interrupt(event):
            if event["type"] == "query_started":
                raise KeyboardInterrupt
        summary = self.run_search(settings(keywords=("Java", "Kotlin", "Go"), sources=("linkedin",)),
                                  on_event=interrupt)
        self.assertEqual(summary.state, "cancelled")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_pipeline -v`
Expected: ERROR `ModuleNotFoundError: No module named 'pipeline'`.

- [ ] **Step 3: Write `src/pipeline.py`**

```python
"""
pipeline.py — One search run, shared by the terminal and the app
==================================================================
run_search() plans every query, runs them on two lanes, saves each query's jobs
to the Store as soon as it finishes, and reports progress as plain-data events
(see docs/superpowers/specs/2026-10-10-core-pipeline-design.md §8).

Lanes (one thread each, queries run one after another inside a lane):
    HTTP lane    : linkedin, indeed          (JobSpy, no browser)
    Browser lane : bayt, naukrigulf, google_jobs, linkedin_posts (one Chrome)

Lanes never touch the Store and never call on_event: they push messages onto a
queue, and the thread that called run_search() does all saving and reporting.
So on_event always runs on the caller's thread, in order.

Cancellation: set the `cancel` event (the terminal does this on Ctrl+C). Each
lane stops before its next query, so cancel takes at most one in-flight query.
"""

import logging
import queue
import threading
from dataclasses import dataclass, field

from browser import ChromeNotFound
from catalog import BROWSER_SOURCES, SOURCE_ORDER, plan_queries
from linkedin_posts import LinkedInLoginRequired
from store import Store

log = logging.getLogger(__name__)

HTTP_SOURCES = ("linkedin", "indeed")
_QUEUE_POLL_SECONDS = 0.2


@dataclass
class RunSummary:
    run_id: int
    state: str  # 'done' | 'cancelled' | 'failed'
    found: int
    new_jobs: list = field(default_factory=list)


def _default_fetchers() -> dict:
    from bayt import fetch_bayt
    from google_jobs import fetch_google_jobs
    from linkedin_posts import fetch_linkedin_posts
    from naukrigulf import fetch_naukrigulf
    from scraper import fetch_indeed, fetch_linkedin

    return {
        "linkedin": fetch_linkedin,
        "indeed": fetch_indeed,
        "bayt": fetch_bayt,
        "naukrigulf": fetch_naukrigulf,
        "google_jobs": fetch_google_jobs,
        "linkedin_posts": fetch_linkedin_posts,
    }


def _default_driver_factory():
    from browser import create_driver
    from paths import linkedin_profile_dir

    return create_driver(headless=False, user_data_dir=str(linkedin_profile_dir()))


def _query_fields(query: dict) -> dict:
    return {
        "index": query["index"],
        "source": query["source"],
        "keyword": query["keyword"],
        "location_id": query["location_id"],
    }


def _driver_alive(driver) -> bool:
    try:
        driver.window_handles
        return True
    except Exception:
        return False


def _quit(driver) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        log.debug("driver.quit() failed", exc_info=True)


def _skip_all(queries, reason, out) -> None:
    for query in queries:
        out.put(("skipped", query, reason))


def _http_lane(queries, fetchers, keywords, cancel, out) -> None:
    seen_urls = set()
    for query in queries:
        if cancel.is_set():
            return
        out.put(("started", query))
        try:
            jobs = fetchers[query["source"]](query["args"], seen_urls, keywords)
        except Exception as exc:
            log.exception("Query failed: %s %r", query["source"], query["args"])
            out.put(("failed", query, type(exc).__name__))
            continue
        out.put(("done", query, jobs))


def _browser_lane(queries, fetchers, driver_factory, keywords, cancel, out) -> None:
    seen_urls = set()
    driver = None
    restarts_left = 1
    login_blocked = False
    try:
        if cancel.is_set():
            return
        try:
            driver = driver_factory()
        except ChromeNotFound:
            log.warning("Google Chrome not found; skipping browser sources")
            out.put(("event", {"type": "browser_failed", "reason": "chrome_not_found"}))
            _skip_all(queries, "browser_unavailable", out)
            return
        except Exception:
            log.exception("Could not start Chrome")
            out.put(("event", {"type": "browser_failed", "reason": "start_failed"}))
            _skip_all(queries, "browser_unavailable", out)
            return

        for i, query in enumerate(queries):
            if cancel.is_set():
                return
            if query["source"] == "linkedin_posts" and login_blocked:
                out.put(("skipped", query, "login_required"))
                continue
            out.put(("started", query))
            try:
                jobs = fetchers[query["source"]](driver, query["args"], seen_urls, keywords)
            except LinkedInLoginRequired as exc:
                log.warning("LinkedIn login required (%s); skipping post searches", exc)
                login_blocked = True
                out.put(("failed", query, "LinkedInLoginRequired"))
                out.put(("event", {"type": "login_required", "source": query["source"]}))
                continue
            except Exception as exc:
                log.exception("Query failed: %s %r", query["source"], query["args"])
                out.put(("failed", query, type(exc).__name__))
                if _driver_alive(driver):
                    continue
                _quit(driver)
                driver = None
                if restarts_left > 0:
                    restarts_left -= 1
                    try:
                        driver = driver_factory()
                        log.info("Chrome restarted after a crash")
                        continue
                    except Exception:
                        log.exception("Could not restart Chrome")
                out.put(("event", {"type": "browser_failed", "reason": "crashed"}))
                _skip_all(queries[i + 1:], "browser_unavailable", out)
                return
            out.put(("done", query, jobs))
    finally:
        _quit(driver)


def _make_emitter(on_event):
    def emit(event: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:
            log.exception("on_event raised for %s", event.get("type"))
    return emit


def run_search(settings: dict, on_event=None, cancel=None, *, country_labels=None,
               store=None, fetchers=None, driver_factory=None) -> RunSummary:
    """
    Run one search. settings = {"keywords", "locations", "sources"}.

    Errors before the run starts (e.g. the database cannot be written) raise.
    After `run_started` it never raises: failures become events and the
    returned state is 'failed'. A KeyboardInterrupt on the calling thread
    cancels the run instead of aborting it.
    """
    cancel = cancel or threading.Event()
    store = store or Store()
    fetchers = fetchers or _default_fetchers()
    driver_factory = driver_factory or _default_driver_factory
    emit = _make_emitter(on_event)
    keywords = list(settings["keywords"])

    order = {source: i for i, source in enumerate(SOURCE_ORDER)}
    queries = plan_queries(keywords, settings["locations"], settings["sources"], country_labels)
    queries.sort(key=lambda q: order[q["source"]])
    for i, query in enumerate(queries):
        query["index"] = i

    run_id = store.start_run({key: list(settings[key]) for key in ("keywords", "locations", "sources")})
    emit({"type": "run_started", "run_id": run_id, "total": len(queries)})

    http_queries = [q for q in queries if q["source"] in HTTP_SOURCES]
    browser_queries = [q for q in queries if q["source"] in BROWSER_SOURCES]
    out = queue.Queue()
    lanes = []
    if http_queries:
        lanes.append(threading.Thread(
            target=_http_lane, args=(http_queries, fetchers, keywords, cancel, out),
            name="hawkeye-http-lane", daemon=True))
    if browser_queries:
        lanes.append(threading.Thread(
            target=_browser_lane, args=(browser_queries, fetchers, driver_factory, keywords, cancel, out),
            name="hawkeye-browser-lane", daemon=True))
    for lane in lanes:
        lane.start()

    totals = {"found": 0, "new_jobs": []}

    def handle(message) -> None:
        kind = message[0]
        if kind == "started":
            emit({"type": "query_started", **_query_fields(message[1])})
        elif kind == "done":
            query, jobs = message[1], message[2]
            for job in jobs:
                job["keyword"] = query["keyword"]
                job["location_id"] = query["location_id"]
            fresh = store.add_jobs(run_id, jobs)
            totals["found"] += len(jobs)
            totals["new_jobs"].extend(fresh)
            emit({"type": "query_done", **_query_fields(query), "found": len(jobs), "new": len(fresh)})
        elif kind == "failed":
            emit({"type": "query_failed", **_query_fields(message[1]), "error": message[2]})
        elif kind == "skipped":
            emit({"type": "query_skipped", **_query_fields(message[1]), "reason": message[2]})
        elif kind == "event":
            emit(message[1])

    state = "done"
    try:
        while True:
            try:
                try:
                    message = out.get(timeout=_QUEUE_POLL_SECONDS)
                except queue.Empty:
                    if not any(lane.is_alive() for lane in lanes) and out.empty():
                        break
                    continue
                handle(message)
            except KeyboardInterrupt:
                log.info("Interrupted: cancelling after in-flight queries")
                cancel.set()
        state = "cancelled" if cancel.is_set() else "done"
    except Exception:
        log.exception("Search run %s failed", run_id)
        cancel.set()
        state = "failed"
    finally:
        for lane in lanes:
            lane.join()

    try:
        store.finish_run(run_id, state, totals["found"], len(totals["new_jobs"]))
    except Exception:
        log.exception("Could not record the end of run %s", run_id)
    emit({"type": "run_finished", "run_id": run_id, "state": state,
          "found": totals["found"], "new": len(totals["new_jobs"])})
    return RunSummary(run_id=run_id, state=state, found=totals["found"], new_jobs=totals["new_jobs"])
```

- [ ] **Step 4: Run the pipeline tests repeatedly to verify they pass and are not flaky**

Run: `for i in 1 2 3 4 5; do venv/bin/python3 -m unittest tests.test_pipeline 2>&1 | tail -1; done`
Expected: `OK` five times.

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline.py tests/test_pipeline.py
git commit -m "Add run_search pipeline: two lanes, per-query saves, events, cancel"
```

---

### Task 10: Terminal front-end on top of the pipeline (`main.py`), remove `tracker.py`

**Files:**
- Modify: `src/main.py` (whole file)
- Delete: `src/tracker.py`
- Create: `tests/test_main.py`

**Interfaces:**
- Consumes:
  - `pipeline.run_search`, `pipeline.RunSummary` (Task 9).
  - `store.Store` (Task 2).
  - `settings.collect_settings`, `settings.migrate_legacy_config` (Task 3).
  - `logs.setup_logging`, `paths.data_dir`, `paths.linkedin_profile_dir` (Task 1).
  - `catalog.SOURCES` (existing).
  - `countries.load_countries`, `countries.CountryFetchError` (existing).
  - `email_sender.send_email` (existing, unchanged).
- Produces:
  - `main.format_event(event: dict, country_labels: dict | None = None) -> str | None`
  - `main.main() -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main.py`:

```python
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main
from pipeline import RunSummary
from store import job_id

SETTINGS = {"keywords": ["Java"], "locations": ["jo"], "sources": ["linkedin"]}
COUNTRIES = [{"id": "remote", "label": "Remote"}, {"id": "jo", "label": "Jordan"}]


class FormatEventTests(unittest.TestCase):
    def test_query_done_uses_site_and_country_names(self):
        line = main.format_event(
            {"type": "query_done", "index": 0, "source": "bayt", "keyword": "Java",
             "location_id": "jo", "found": 3, "new": 1},
            {"jo": "Jordan"},
        )
        self.assertEqual(line, "  [Bayt.com] 'Java' in Jordan: 3 found, 1 new")

    def test_query_started_prints_nothing(self):
        self.assertIsNone(main.format_event(
            {"type": "query_started", "index": 0, "source": "bayt", "keyword": "Java", "location_id": "jo"}))

    def test_failure_points_to_log(self):
        line = main.format_event(
            {"type": "query_failed", "index": 0, "source": "linkedin_posts", "keyword": "Java",
             "location_id": None, "error": "TimeoutException"})
        self.assertIn("TimeoutException", line)
        self.assertIn("hawkeye.log", line)
        self.assertNotIn(" in ", line.split(":")[0])

    def test_chrome_missing_is_explained(self):
        line = main.format_event({"type": "browser_failed", "reason": "chrome_not_found"})
        self.assertIn("Chrome", line)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.env = patch.dict(os.environ, {"HAWKEYE_DATA_DIR": str(Path(self.tmp.name) / "data")})
        self.env.start()
        self.patches = [
            patch("main.ROOT", self.root),
            patch("main._load_dotenv"),
            patch("main.setup_logging"),
            patch("main.collect_settings", return_value=SETTINGS),
            patch("main.load_countries", return_value=COUNTRIES),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_cancelled_run_still_emails_new_jobs(self):
        job = {"title": "Java Developer", "url": "https://a.com/1", "description": "Acme", "source": "Bayt"}
        summary = RunSummary(run_id=1, state="cancelled", found=1, new_jobs=[job])
        with patch("main.run_search", return_value=summary), patch("main.send_email") as send:
            main.main()
        send.assert_called_once_with([job])

    def test_no_new_jobs_sends_nothing(self):
        with patch("main.run_search", return_value=RunSummary(1, "done", 0, [])), \
             patch("main.send_email") as send:
            main.main()
        send.assert_not_called()

    def test_legacy_seen_jobs_are_not_reported_again(self):
        old_url = "https://www.linkedin.com/jobs/view/111"
        (self.root / "seen_jobs.json").write_text(json.dumps({"seen": [job_id(old_url)], "count": 1}), encoding="utf-8")

        def fake_linkedin(args, seen_urls, keywords):
            return [
                {"title": "Java Developer", "url": old_url, "description": "Old", "source": "Linkedin"},
                {"title": "Java Developer", "url": "https://www.linkedin.com/jobs/view/222",
                 "description": "New", "source": "Linkedin"},
            ]

        with patch("pipeline._default_fetchers", return_value={"linkedin": fake_linkedin}), \
             patch("main.send_email") as send:
            main.main()
        emailed = send.call_args.args[0]
        self.assertEqual([j["url"] for j in emailed], ["https://www.linkedin.com/jobs/view/222"])

    def test_legacy_config_copied_into_data_dir(self):
        (self.root / "config.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
        with patch("main.run_search", return_value=RunSummary(1, "done", 0, [])), patch("main.send_email"):
            main.main()
        self.assertTrue((Path(os.environ["HAWKEYE_DATA_DIR"]) / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_main -v`
Expected: ERROR `ImportError: cannot import name 'scrape_all' from 'scraper'`. The old `main.py` still imports the removed functions.

- [ ] **Step 3: Rewrite `src/main.py`**

```python
#!/usr/bin/env python3
"""
main.py — hawkeye-jobs terminal front-end
==========================================
Prompts for settings (or reuses the saved ones), runs pipeline.run_search(),
prints progress and the new jobs, and emails the new ones.

Ctrl+C stops the search after the queries already running; jobs found so far
are kept, printed and emailed. Runtime files live in the data directory
(paths.data_dir()), not in the repo.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from catalog import SOURCES
from countries import CountryFetchError, load_countries
from email_sender import send_email
from logs import setup_logging
from paths import data_dir, linkedin_profile_dir
from pipeline import run_search
from settings import collect_settings, migrate_legacy_config
from store import Store

ROOT = Path(__file__).resolve().parent.parent

_BROWSER_FAILED = {
    "chrome_not_found": "⚠ Google Chrome is not installed — skipping Bayt, NaukriGulf, Google Jobs and LinkedIn posts.",
    "start_failed": "⚠ Could not start Chrome — skipping browser sources (details in hawkeye.log).",
    "crashed": "⚠ Chrome crashed twice — skipping the remaining browser searches.",
}


def _load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _query_label(event: dict, country_labels: dict) -> str:
    site = SOURCES.get(event["source"], event["source"])
    label = f"  [{site}] '{event['keyword']}'"
    loc_id = event.get("location_id")
    if loc_id:
        label += f" in {country_labels.get(loc_id, loc_id)}"
    return label


def format_event(event: dict, country_labels=None):
    """English terminal line for a pipeline event, or None to print nothing."""
    country_labels = country_labels or {}
    kind = event["type"]
    if kind == "run_started":
        return f"[hawkeye-jobs] Running {event['total']} searches… (Ctrl+C to stop)"
    if kind == "query_done":
        return f"{_query_label(event, country_labels)}: {event['found']} found, {event['new']} new"
    if kind == "query_failed":
        return f"{_query_label(event, country_labels)}: failed ({event['error']}) — details in hawkeye.log"
    if kind == "query_skipped":
        return f"{_query_label(event, country_labels)}: skipped ({event['reason'].replace('_', ' ')})"
    if kind == "login_required":
        return (
            "⚠ Not logged into LinkedIn — skipping post search.\n"
            f'  Log in once: open Chrome with --user-data-dir="{linkedin_profile_dir()}", '
            "sign in to LinkedIn, then close it."
        )
    if kind == "browser_failed":
        return _BROWSER_FAILED.get(event["reason"], f"⚠ Browser problem: {event['reason']}")
    if kind == "run_finished":
        return f"\n[hawkeye-jobs] Search {event['state']}: {event['found']} found, {event['new']} new"
    return None


def _print_jobs(jobs: list):
    print("\n── New jobs ──")
    for i, job in enumerate(jobs, start=1):
        print(f"{i}. {job.get('title', '(no title)')}")
        print(f"   {job.get('description', '')}")
        print(f"   {job.get('source', '')} · {job.get('url', '')}")


def main():
    _load_dotenv()
    setup_logging()
    print("=" * 50)
    print("🦅 hawkeye-jobs")
    print("=" * 50)

    if migrate_legacy_config(ROOT):
        print("Copied your saved search settings into the data folder.")
    store = Store()
    store.recover_interrupted_runs()
    if store.import_legacy_seen_file(ROOT / "seen_jobs.json"):
        print("Imported your previously seen jobs into the data folder.")
    print(f"Data folder: {data_dir()}")

    chosen = collect_settings()
    try:
        countries = load_countries()
    except CountryFetchError:
        countries = []
    country_labels = {c["id"]: c["label"] for c in countries}

    def print_event(event):
        line = format_event(event, country_labels)
        if line:
            print(line)

    summary = run_search(chosen, on_event=print_event, country_labels=country_labels, store=store)
    store.close()

    if summary.state == "failed":
        print("[main] The search stopped because of an error — see hawkeye.log in the data folder.")
    if not summary.new_jobs:
        print("[main] No new jobs to report")
        return

    _print_jobs(summary.new_jobs)
    print(f"\n[main] Sending email for {len(summary.new_jobs)} new jobs...")
    send_email(summary.new_jobs)

    print("=" * 50)
    print("🦅 hawkeye-jobs done!")
    print("=" * 50)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Delete the tracker and confirm nothing references it or the old functions**

```bash
git rm src/tracker.py
grep -rn "tracker\|scrape_all\|scrape_bayt\|scrape_naukrigulf\|scrape_google_jobs\|scrape_linkedin_posts\|filter_new_jobs" src tests
```

The grep will match one line in the `src/utils.py` docstring: `Imported by: scraper.py, bayt.py, naukrigulf.py, tracker.py`. Change it to `Imported by: scraper.py, bayt.py, naukrigulf.py, store.py` and re-run the grep.
Expected: no output.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/main.py src/utils.py tests/test_main.py
git commit -m "Terminal mode runs on the shared pipeline; remove tracker.py"
```

---

### Task 11: Documentation, end-to-end check, after timing

**Files:**
- Modify: `README.md`, `docs/ARCHITECTURE.md`, `CLAUDE.md`, `.gitignore`
- Modify: `docs/superpowers/plans/2026-10-10-core-pipeline-timings.md`

**Interfaces:**
- Consumes: everything above. There are no code changes in this task, unless the end-to-end run finds a bug. In that case, fix it with a test first, in the task that owns that code.

- [ ] **Step 1: Update `README.md`**

In the Features list:
- Replace `- Remembers your last choices in \`config.json\`` with `- Remembers your last choices`.
- Replace `- Remembers seen jobs so you are not notified twice` with `- Remembers every job it has found (with a status: new / saved / applied / dismissed) so you are not notified twice`.
- Add `- Ctrl+C stops a search cleanly; jobs found so far are kept`.

Add this section right after the "Run" section:

```markdown
## Where your data lives

Everything hawkeye-jobs saves is kept in one folder on your computer:

| OS | Folder |
|---|---|
| macOS | `~/Library/Application Support/hawkeye-jobs` |
| Windows | `C:\Users\<you>\AppData\Local\hawkeye-jobs` |
| Linux | `~/.local/share/hawkeye-jobs` |

It contains `hawkeye.db` (jobs and searches), `config.json` (your search settings), `hawkeye.log` (attach this when reporting a problem), and `linkedin-profile/` (the Chrome profile used for LinkedIn posts).

Set `HAWKEYE_DATA_DIR` to use a different folder. If you used hawkeye-jobs before this change, your old `config.json` and `seen_jobs.json` are imported automatically the first time you run it. The old files are left in place.

**Reset** (forget all jobs and get notified again): delete `hawkeye.db` from that folder.

**LinkedIn posts** need a one-time login. Open Chrome with the profile the tool uses, sign in to LinkedIn, and close it:

    open -a "Google Chrome" --args --user-data-dir="$HOME/Library/Application Support/hawkeye-jobs/linkedin-profile"

If you already have `~/.hawkeye-linkedin-profile` from an older version, it keeps being used.

**Chrome** is found automatically on macOS, Windows, and Linux, and its version is detected for you. Set `HAWKEYE_CHROME_MAJOR` only if detection picks the wrong version.
```

Remove any other README sentence that tells the user to delete `seen_jobs.json`, `config.json`, or `countries_cache.json` from the repo folder. Use `grep -n "seen_jobs\|countries_cache\|rm -f" README.md` to find them.

- [ ] **Step 2: Update `CLAUDE.md`**

Make these replacements:
- Pipeline step 5 → `5. **\`src/pipeline.py\`** — \`run_search()\`: plans queries (\`catalog.plan_queries\`), runs an HTTP lane (JobSpy) and a browser lane (Chrome) in parallel, saves each query's jobs to **\`src/store.py\`** (SQLite, statuses), emits progress events, supports cancel. **\`src/filters.py\`** — title/post matching. **\`src/email_sender.py\`** — Gmail SMTP.`
- Step 3 → `3. **\`src/scraper.py\`** — JobSpy \`fetch_linkedin\` / \`fetch_indeed\` (no browser). 48h window.`
- In the Key files table:
  - Replace the `src/browser.py` row with `| \`src/browser.py\` | Chrome discovery (\`find_chrome\`), auto-detected version, \`wait_for\`, persistent profile |`.
  - Add rows: `| \`src/pipeline.py\` | Run orchestration, events, cancel |`, `| \`src/store.py\` | SQLite schema, job statuses, dedup |`, `| \`src/paths.py\` | Data directory and file locations |`.
- Replace the line `Last search settings are stored in \`config.json\` (gitignored).` with `Runtime files (\`hawkeye.db\`, \`config.json\`, \`hawkeye.log\`, LinkedIn profile) live in the per-OS data directory from \`src/paths.py\` (\`HAWKEYE_DATA_DIR\` overrides it).`
- Replace the Resetting block with:

```bash
# macOS — Linux: ~/.local/share/hawkeye-jobs — Windows: %LOCALAPPDATA%\hawkeye-jobs
rm -f ~/Library/Application\ Support/hawkeye-jobs/hawkeye.db
```

- In the Tests line, change `python3` to `venv/bin/python3`.

- [ ] **Step 3: Update `docs/ARCHITECTURE.md`**

- **Directory Layout:**
  - Add `pipeline.py`, `store.py`, `paths.py`, `logs.py` with one-line comments, matching the module docstrings.
  - Remove `tracker.py`.
  - Replace the paragraph under the tree with: `Runtime files live in the per-OS data directory (see src/paths.py), never in the repo: hawkeye.db, config.json, countries_cache.json, hawkeye.log, linkedin-profile/.`
- **Pipeline Flow:** replace the diagram and the `seen_urls` paragraph with a short description of `main.py → collect_settings() → run_search()`: two lanes, queue, per-query `store.add_jobs`, the events table copied from spec §8, and the cancel behaviour.
- **`src/browser.py` section:** replace the `version_main=149` bullets and "To update Chrome version" with a description of `find_chrome()`, version auto-detection (registry on Windows), `HAWKEYE_CHROME_MAJOR`, `ChromeNotFound`, and `wait_for()`.
- **Bayt / NaukriGulf / Google Jobs:** replace each `time.sleep(N)` note with the `wait_for` selector and timeout (20 / 15 / 15 s), and rename the entry point to `fetch_*()`, which raises on error.
- **LinkedIn posts:**
  - Replace the "First-time setup" command's path with `paths.linkedin_profile_dir()` and the resolution order from spec §4.
  - Say it raises `LinkedInLoginRequired`.
- **`src/tracker.py` section:** replace it with a `src/store.py` section summarising spec §5: tables, statuses, `add_jobs` semantics, legacy import, threading rule.
- **Job Dict Schema:** `keyword` is now the user keyword that found the job. Add `location_id`.
- **Common Maintenance Tasks:**
  - Delete "Update Chrome version".
  - Under "Fix broken selectors", add: the `CARD_SELECTOR` constant in each scraper must match the job-card selector the parser uses.

- [ ] **Step 4: Stop ignoring the repo-root runtime files that no longer exist**

In `.gitignore`, under `# Runtime data`, keep `seen_jobs.json`, `config.json`, `countries_cache.json`. Existing users still have them in the repo and they must never be committed. Add `hawkeye.db` and `hawkeye.db-*` below them, for anyone who sets `HAWKEYE_DATA_DIR=.`.

- [ ] **Step 5: Full test suite**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all PASS. Note the final count, which is the 67 existing tests plus the new ones.

- [ ] **Step 6: End-to-end terminal run (real sites), with timing**

Email disabled, same `config.json` as the baseline. The legacy config and seen jobs are imported on this first run. Use the same command as the baseline, not `bash run.sh`: `run.sh` sources `.env`, which would override the empty `GMAIL_*` values and send a real email.

```bash
time (echo y | GMAIL_USER= GMAIL_PASS= venv/bin/python3 src/main.py) 2>&1 | tail -40
```

Expected:
- `Data folder: ...` is printed.
- One line per query, `run_finished` shows `Search done`.
- No traceback.
- Write down the `real` time.

- [ ] **Step 7: Second run reports no new jobs**

Run the same command again.
Expected: `[main] No new jobs to report` (or only jobs that genuinely appeared in between). Each query line shows `0 new` for jobs seen in the first run.

Then check that nothing new was written in the repo root:

```bash
git status --short --ignored | grep -v "^!! venv/\|__pycache__"
```

Expected:
- No new entries besides the files this plan changed.
- `!! seen_jobs.json`, `!! config.json` and `!! countries_cache.json` may still be listed. They are the old files, left in place on purpose.
- There must be **no** `hawkeye.db` or `hawkeye.log` in the repo root.

- [ ] **Step 8: Ctrl+C check**

Start the same command without `time`. When the first browser query lines appear, press Ctrl+C once.
Expected:
- The run ends within ~35 s with `Search cancelled`.
- The Chrome window closes.
- New jobs found so far are printed.
- No traceback.

- [ ] **Step 9: Record the after timing**

Fill in the "After" row of `docs/superpowers/plans/2026-10-10-core-pipeline-timings.md` with the commit (`git rev-parse --short HEAD`) and the `real` time from Step 6.

- [ ] **Step 10: Commit**

```bash
git add README.md CLAUDE.md docs/ARCHITECTURE.md .gitignore docs/superpowers/plans/2026-10-10-core-pipeline-timings.md
git commit -m "Document data directory, pipeline and store; record after timing"
```
