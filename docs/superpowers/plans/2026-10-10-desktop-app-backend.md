# Desktop App Backend Implementation Plan (Plan 2a of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build everything behind the desktop window (local API, run manager, auto-search, notifications, reporting, schema v3, app launcher), so Plan 2b only has to add the HTML/JS front-end.

**Architecture:**
- `app.py` starts a FastAPI app on `127.0.0.1:<free port>` in a thread and opens a pywebview window.
- Every `/api/*` call needs a per-launch random token, and every request needs an allowed `Host`.
- `runner.RunManager` is the only thing that starts searches. It holds an in-process lock plus an OS file lock shared with terminal mode, runs `pipeline.run_search` on a worker thread, records per-query results (schema v3), fans events out to SSE subscribers, and reports new jobs through a notifier.
- `runner.AutoSearch` ticks every 60 s.

**Tech stack:**
- Python 3.11+ (venv is 3.14) and stdlib `threading`/`queue`/`sqlite3`/`fcntl`/`msvcrt`.
- FastAPI 0.143, uvicorn 0.54, pywebview 6.2, desktop-notifier 6.2, Babel 2.18, httpx2 (for FastAPI's TestClient).
- unittest.

**Spec:** `docs/superpowers/specs/2026-10-10-desktop-app-design.md`. Read §3–§8 before starting. Approved mockups are in `docs/superpowers/specs/mockups/2026-10-10-app-ui/`.

**Plan 2b (front-end)** is written after this plan lands. It replaces the placeholder `src/web/static/index.html` created here.

## Global Constraints

- Use `venv/bin/python3` for everything. `venv/bin/pip` is broken, so install with `venv/bin/python3 -m pip install -r requirements.txt`.
- Run tests with `venv/bin/python3 -m unittest discover -s tests -v`. 258 tests pass at the start, and the output must stay pristine.
- Every new test file starts with:
  ```python
  sys.path.insert(0, str(Path(__file__).resolve().parent))
  import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path
  ```
  `tests/test_isolation.py` enforces this.
- Tests never use the network and never start Chrome, a real window, or a real notification. Never run `src/main.py`, `src/app.py` or `run.sh` for real.
- Everything runs locally:
  - the server binds `127.0.0.1` only;
  - `/api/*` requires `X-Hawkeye-Token` (or `?token=` on `GET /api/events` only);
  - every request needs `Host` in the allowed set, otherwise **403**.
- Never two searches at once (app, auto-search, terminal). Never lose a job notification. Keep the per-site throttling untouched.
- The backend never sends display text to the UI. Events and errors are data or codes; the front-end translates them.
- UI language codes are exactly `"ar"` and `"en"`. Themes are exactly `"auto"`, `"light"`, `"dark"`. Auto-search hours are exactly one of `2, 4, 6, 12, 24` (default 6). Run triggers are exactly `"manual"`, `"auto"`, `"terminal"`.
- App settings live under `config.json → "app"`. The search keys `keywords`/`locations`/`sources` stay top-level.
- `REPORT_LIMIT = 500`.
- Job fields are untrusted scraped text. `/api/open-url` only opens `http(s)` URLs.
- Commits end with a blank line and a `Co-Authored-By:` trailer naming the model you run as.
- Follow the existing style: module docstrings as `name.py — summary` + `====`, `log = logging.getLogger(__name__)`.

## Review Focus

1. **The terminal starts a search while the app is mid-search** (or the reverse): the second one must be refused cleanly — terminal prints and exits 0, app returns 409 — never run concurrently. Tests: Task 3 `test_second_holder_is_refused_until_release` / `test_lock_held_by_other_process_is_refused`, Task 3 `test_main_refuses_when_app_holds_the_run_lock`, Task 6 `test_start_refused_when_file_lock_held_elsewhere`.
2. **The app is killed or closed mid-run:** jobs already saved but not yet notified must be notified on the next launch. Test: Task 6 `test_report_pending_now_notifies_leftovers`.
3. **A web page in the user's normal browser tries to call the local API** (no token, or DNS rebinding with a foreign `Host`): it must get 403. Tests: Task 9 `test_api_without_token_is_forbidden`, `test_foreign_host_is_forbidden_even_for_static`.
4. **"Report a problem" text leaks the user's name, home path, e-mail or secrets.** Test: Task 11 `test_redact_removes_personal_data`.
5. **Notifications denied or unsupported on the OS:** jobs must not pile up forever; they are marked reported and stay visible as New. Test: Task 5 `test_backend_failure_still_counts_as_reported`.

---

### Task 1: Dependencies, version, app settings, atomic config writes

**Files:**
- Modify: `requirements.txt`, `src/settings.py`
- Create: `src/version.py`, `tests/test_app_settings.py`

**Interfaces:**
- Produces (used by Tasks 6, 7, 9–12):
  - `version.__version__ = "1.0.0"`
  - `settings.APP_DEFAULTS`, `settings.LANGUAGES = ("ar", "en")`, `settings.THEMES = ("auto", "light", "dark")`, `settings.AUTO_HOURS = (2, 4, 6, 12, 24)`, `settings.USER_APP_KEYS = ("language", "theme", "auto_search", "onboarding_done", "update_check")`
  - `settings.load_app_settings(path=None) -> dict`: a full dict, defaults merged
  - `settings.save_app_settings(patch: dict, path=None) -> dict`: validates, merges, writes, returns the full settings; raises `SettingsError`
  - `settings.SettingsError(ValueError)` with `.errors: dict[str, str]` (field → error code)
  - `settings.validate_search_settings(data: dict, allowed_locations: set[str]) -> dict`: a cleaned `{keywords, locations, sources}`; raises `SettingsError`
  - `save_config` now writes atomically through `_write_json_atomic(path, data)`

- [ ] **Step 1: Install dependencies**

Append to `requirements.txt`:

```
fastapi
uvicorn
pywebview
desktop-notifier
babel
httpx2
```

Run: `venv/bin/python3 -m pip install -r requirements.txt`
Expected: everything installs or is "already satisfied". FastAPI 0.143 / uvicorn 0.54 / pywebview 6.2 / desktop-notifier 6.2 / Babel 2.18 / httpx2 2.13 were verified on this machine.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_app_settings.py`:

```python
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import settings
from version import __version__


class VersionTests(unittest.TestCase):
    def test_version_is_semver(self):
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts))


class AppSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_file_missing(self):
        app = settings.load_app_settings(self.path)
        self.assertEqual(app["language"], "ar")
        self.assertEqual(app["theme"], "auto")
        self.assertEqual(app["auto_search"], {"enabled": True, "hours": 6})
        self.assertFalse(app["onboarding_done"])
        self.assertTrue(app["update_check"])
        self.assertIsNone(app["last_posts_run"])

    def test_invalid_stored_values_fall_back_to_defaults(self):
        self.path.write_text(json.dumps({"app": {"language": "fr", "auto_search": {"enabled": "yes", "hours": 5}}}),
                             encoding="utf-8")
        app = settings.load_app_settings(self.path)
        self.assertEqual(app["language"], "ar")
        self.assertEqual(app["auto_search"], {"enabled": True, "hours": 6})

    def test_save_merges_and_keeps_search_keys(self):
        self.path.write_text(json.dumps({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}),
                             encoding="utf-8")
        app = settings.save_app_settings({"language": "en", "auto_search": {"enabled": False, "hours": 12}}, self.path)
        self.assertEqual(app["language"], "en")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["keywords"], ["Java"])
        self.assertEqual(stored["app"]["auto_search"], {"enabled": False, "hours": 12})
        self.assertEqual(settings.load_app_settings(self.path)["theme"], "auto")

    def test_save_rejects_bad_values_with_field_errors(self):
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.save_app_settings({"language": "fr", "auto_search": {"enabled": True, "hours": 5},
                                        "nonsense": 1}, self.path)
        self.assertEqual(ctx.exception.errors, {"language": "invalid", "auto_search": "invalid",
                                                "nonsense": "unknown"})
        self.assertFalse(self.path.exists())

    def test_internal_fields_can_be_saved(self):
        app = settings.save_app_settings({"last_posts_run": "2026-10-10T08:00:00Z",
                                          "last_update_check": "2026-10-10T08:00:00Z",
                                          "latest_version": "1.0.1"}, self.path)
        self.assertEqual(app["latest_version"], "1.0.1")

    def test_atomic_write_leaves_no_temp_files(self):
        settings.save_app_settings({"theme": "dark"}, self.path)
        settings.save_config({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}, self.path)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["config.json"])
        self.assertEqual(settings.load_app_settings(self.path)["theme"], "dark")


class ValidateSearchSettingsTests(unittest.TestCase):
    ALLOWED = {"jo", "sa", "remote"}

    def test_cleans_and_dedupes(self):
        cleaned = settings.validate_search_settings(
            {"keywords": [" Java ", "Java", "Spring"], "locations": ["JO", "remote"], "sources": ["bayt", "linkedin"]},
            self.ALLOWED)
        self.assertEqual(cleaned, {"keywords": ["Java", "Spring"], "locations": ["jo", "remote"],
                                   "sources": ["bayt", "linkedin"]})

    def test_reports_every_bad_field(self):
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.validate_search_settings({"keywords": [], "locations": ["xx"], "sources": ["myspace"]},
                                              self.ALLOWED)
        self.assertEqual(ctx.exception.errors, {"keywords": "required", "locations": "invalid",
                                                "sources": "invalid"})

    def test_limits_keyword_count_and_length(self):
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.validate_search_settings({"keywords": ["x" * 101], "locations": ["jo"], "sources": ["bayt"]},
                                              self.ALLOWED)
        self.assertEqual(ctx.exception.errors, {"keywords": "too_long"})
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.validate_search_settings({"keywords": [f"k{i}" for i in range(21)], "locations": ["jo"],
                                               "sources": ["bayt"]}, self.ALLOWED)
        self.assertEqual(ctx.exception.errors, {"keywords": "too_many"})

    def test_non_list_fields_are_invalid(self):
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.validate_search_settings({"keywords": "Java", "locations": None}, self.ALLOWED)
        self.assertEqual(ctx.exception.errors, {"keywords": "invalid", "locations": "invalid",
                                                "sources": "invalid"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_app_settings -v`
Expected: ERROR `ModuleNotFoundError: No module named 'version'`.

- [ ] **Step 4: Create `src/version.py`**

```python
"""version.py — The app version shown in Settings and compared with GitHub releases."""

__version__ = "1.0.0"
```

- [ ] **Step 5: Extend `src/settings.py`**

Add `import os` and `import tempfile` to the imports. Add `SOURCE_ORDER` is already imported from catalog; keep it. Below `SEARCH_KEYS = ...` add:

```python
LANGUAGES = ("ar", "en")
THEMES = ("auto", "light", "dark")
AUTO_HOURS = (2, 4, 6, 12, 24)
USER_APP_KEYS = ("language", "theme", "auto_search", "onboarding_done", "update_check")
_INTERNAL_APP_KEYS = ("last_update_check", "latest_version", "last_posts_run")
APP_DEFAULTS = {
    "language": "ar",
    "theme": "auto",
    "auto_search": {"enabled": True, "hours": 6},
    "onboarding_done": False,
    "update_check": True,
    "last_update_check": None,
    "latest_version": None,
    "last_posts_run": None,
}
MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 100


class SettingsError(ValueError):
    """Invalid settings. `errors` maps each bad field to a code: required / invalid / too_long / too_many / unknown."""

    def __init__(self, errors: dict):
        super().__init__(", ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors
```

Replace `save_config` with this version and add the helpers below it:

```python
def _read_config_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write to a temp file in the same folder, then rename — a crash never leaves half a file."""
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_config(data: dict, path=None) -> None:
    """Write keywords/locations/sources, keeping any other keys already in the file (e.g. the app's settings)."""
    path = Path(path) if path else config_path()
    existing = _read_config_dict(path)
    for key in SEARCH_KEYS:
        existing[key] = list(data[key])
    _write_json_atomic(path, existing)


def _valid_app_value(key: str, value) -> bool:
    if key == "language":
        return value in LANGUAGES
    if key == "theme":
        return value in THEMES
    if key == "auto_search":
        return (isinstance(value, dict) and set(value) == {"enabled", "hours"}
                and isinstance(value["enabled"], bool) and value["hours"] in AUTO_HOURS)
    if key in ("onboarding_done", "update_check"):
        return isinstance(value, bool)
    if key in _INTERNAL_APP_KEYS:
        return value is None or isinstance(value, str)
    return False


def load_app_settings(path=None) -> dict:
    """The app's settings with defaults filled in; invalid stored values fall back to defaults."""
    path = Path(path) if path else config_path()
    stored = _read_config_dict(path).get("app")
    stored = stored if isinstance(stored, dict) else {}
    result = json.loads(json.dumps(APP_DEFAULTS))  # deep copy
    for key in APP_DEFAULTS:
        if key in stored and _valid_app_value(key, stored[key]):
            result[key] = stored[key]
    return result


def save_app_settings(patch: dict, path=None) -> dict:
    """Validate `patch`, merge it into config.json → "app", write atomically, return the full settings."""
    path = Path(path) if path else config_path()
    errors = {}
    for key, value in patch.items():
        if key not in APP_DEFAULTS:
            errors[key] = "unknown"
        elif not _valid_app_value(key, value):
            errors[key] = "invalid"
    if errors:
        raise SettingsError(errors)
    data = _read_config_dict(path)
    app = load_app_settings(path)
    app.update(patch)
    data["app"] = app
    _write_json_atomic(path, data)
    return app


def validate_search_settings(data: dict, allowed_locations: set) -> dict:
    """Clean and validate search settings coming from the UI. Raises SettingsError with every bad field."""
    errors = {}
    keywords = data.get("keywords")
    cleaned_keywords = []
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        errors["keywords"] = "invalid"
    else:
        for keyword in (k.strip() for k in keywords):
            if keyword and keyword not in cleaned_keywords:
                cleaned_keywords.append(keyword)
        if not cleaned_keywords:
            errors["keywords"] = "required"
        elif any(len(k) > MAX_KEYWORD_LENGTH for k in cleaned_keywords):
            errors["keywords"] = "too_long"
        elif len(cleaned_keywords) > MAX_KEYWORDS:
            errors["keywords"] = "too_many"

    locations = data.get("locations")
    cleaned_locations = []
    if not isinstance(locations, list) or not all(isinstance(x, str) for x in locations):
        errors["locations"] = "invalid"
    else:
        for loc in (normalize_location_id(x) for x in locations):
            if loc and loc not in cleaned_locations:
                cleaned_locations.append(loc)
        if not cleaned_locations:
            errors["locations"] = "required"
        elif any(loc not in allowed_locations for loc in cleaned_locations):
            errors["locations"] = "invalid"

    sources = data.get("sources")
    cleaned_sources = []
    if not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
        errors["sources"] = "invalid"
    else:
        for source in sources:
            if source not in cleaned_sources:
                cleaned_sources.append(source)
        if not cleaned_sources:
            errors["sources"] = "required"
        elif any(s not in SOURCE_ORDER for s in cleaned_sources):
            errors["sources"] = "invalid"

    if errors:
        raise SettingsError(errors)
    return {"keywords": cleaned_keywords, "locations": cleaned_locations, "sources": cleaned_sources}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass (258 + 11).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt src/version.py src/settings.py tests/test_app_settings.py
git commit -m "Add app settings, version and atomic config writes"
```

---

### Task 2: Store schema v3 (run_queries, run trigger) and pipeline trigger

**Files:**
- Modify: `src/store.py`, `src/pipeline.py:334` (the `store.start_run(...)` line and the `run_search` signature), `src/main.py` (pass `trigger="terminal"`)
- Test: `tests/test_store.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces (used by Tasks 6, 7, 10):
  - `store.TRIGGERS = ("manual", "auto", "terminal")`, `SCHEMA_VERSION = 3`
  - `Store.start_run(settings, trigger="manual", search_id=1) -> int`
  - `Store.record_query(run_id: int, event: dict) -> None`: accepts `query_done` / `query_failed` / `query_skipped` events and ignores other types
  - `Store.run_details(run_id) -> dict | None`: the run row (with `settings`) plus `queries: list[dict]` ordered by `idx`
  - `Store.job_counts() -> dict`: `{"new": n, "saved": n, "applied": n, "dismissed": n}`
  - `Store.count_jobs(status=None, source=None, run_id=None, text=None, include_dismissed=False) -> int`: the same filters as `list_jobs`
  - `Store.last_finished_run(trigger=None) -> dict | None`
  - `pipeline.run_search(..., trigger="manual")`, passed through to `start_run`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py` (above the `__main__` guard):

```python
class SchemaV3Tests(StoreTestCase):
    def test_fresh_db_is_version_3_with_run_queries(self):
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
        run_cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
        self.assertIn("trigger", run_cols)
        self.assertIn("search_id", run_cols)
        self.assertTrue(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_queries'").fetchone())
        conn.close()

    def test_v2_db_upgrades_keeping_runs(self):
        self.store.close()
        conn = sqlite3.connect(self.db)
        conn.execute("ALTER TABLE runs DROP COLUMN trigger")
        conn.execute("ALTER TABLE runs DROP COLUMN search_id")
        conn.execute("DROP TABLE run_queries")
        conn.execute("INSERT INTO runs (started_at, state, settings_json) VALUES ('2026-01-01T00:00:00Z','done','{}')")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()
        self.store = Store(self.db)
        runs = self.store.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["trigger"], runs[0]["search_id"]), ("manual", 1))

    def test_start_run_records_trigger_and_rejects_unknown(self):
        run_id = self.store.start_run(SETTINGS, trigger="auto")
        self.assertEqual(self.store.list_runs()[0]["trigger"], "auto")
        self.assertEqual(self.store.list_runs()[0]["id"], run_id)
        with self.assertRaises(ValueError):
            self.store.start_run(SETTINGS, trigger="cron")

    def test_record_query_and_run_details(self):
        run_id = self.store.start_run(SETTINGS)
        base = {"source": "bayt", "keyword": "Java", "location_id": "jo"}
        self.store.record_query(run_id, {"type": "query_done", "index": 1, **base, "found": 4, "new": 2})
        self.store.record_query(run_id, {"type": "query_failed", "index": 0, **base, "error": "TimeoutException"})
        self.store.record_query(run_id, {"type": "query_skipped", "index": 2, **base, "reason": "login_required"})
        self.store.record_query(run_id, {"type": "query_started", "index": 3, **base})
        details = self.store.run_details(run_id)
        self.assertEqual([q["idx"] for q in details["queries"]], [0, 1, 2])
        self.assertEqual([q["status"] for q in details["queries"]], ["failed", "done", "skipped"])
        self.assertEqual(details["queries"][0]["detail"], "TimeoutException")
        self.assertEqual((details["queries"][1]["found"], details["queries"][1]["new"]), (4, 2))
        self.assertEqual(details["settings"], SETTINGS)
        self.assertIsNone(self.store.run_details(run_id + 99))

    def test_job_counts_and_count_jobs(self):
        run_id = self.store.start_run(SETTINGS)
        self.store.add_jobs(run_id, [make_job("https://a.com/1"), make_job("https://a.com/2"),
                                     make_job("https://a.com/3", source="Linkedin")])
        self.store.set_status(job_id("https://a.com/1"), "saved")
        self.store.set_status(job_id("https://a.com/2"), "dismissed")
        self.assertEqual(self.store.job_counts(), {"new": 1, "saved": 1, "applied": 0, "dismissed": 1})
        self.assertEqual(self.store.count_jobs(), 2)
        self.assertEqual(self.store.count_jobs(include_dismissed=True), 3)
        self.assertEqual(self.store.count_jobs(source="Linkedin"), 1)

    def test_last_finished_run(self):
        self.assertIsNone(self.store.last_finished_run())
        manual = self.store.start_run(SETTINGS)
        self.store.finish_run(manual, "done", 1, 1)
        auto = self.store.start_run(SETTINGS, trigger="auto")
        self.store.finish_run(auto, "cancelled", 0, 0)
        self.store.start_run(SETTINGS)  # still running: ignored
        self.assertEqual(self.store.last_finished_run()["id"], auto)
        self.assertEqual(self.store.last_finished_run(trigger="manual")["id"], manual)
```

Append to `tests/test_pipeline.py` (inside `HappyPathTests`):

```python
    def test_trigger_is_recorded_on_the_run(self):
        summary = self.run_search(settings(sources=("linkedin",)), trigger="auto")
        self.assertEqual(self.store.list_runs()[0]["id"], summary.run_id)
        self.assertEqual(self.store.list_runs()[0]["trigger"], "auto")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_store tests.test_pipeline -v`
Expected: FAIL. `user_version` is 2, `trigger` is missing, `record_query` and `run_details` are missing, and `run_search` rejects `trigger=`.

- [ ] **Step 3: Implement schema v3 in `src/store.py`**

Add `import re` to the imports. Next to `_V2_STATEMENTS` add:

```python
TRIGGERS = ("manual", "auto", "terminal")
_QUERY_STATUS = {"query_done": "done", "query_failed": "failed", "query_skipped": "skipped"}

_V3_STATEMENTS = [
    "ALTER TABLE runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual' "
    "CHECK (trigger IN ('manual','auto','terminal'))",
    "ALTER TABLE runs ADD COLUMN search_id INTEGER NOT NULL DEFAULT 1",
    """CREATE TABLE IF NOT EXISTS run_queries (
      run_id      INTEGER NOT NULL REFERENCES runs(id),
      idx         INTEGER NOT NULL,
      source      TEXT NOT NULL,
      keyword     TEXT NOT NULL DEFAULT '',
      location_id TEXT,
      status      TEXT NOT NULL CHECK (status IN ('done','failed','skipped')),
      found       INTEGER NOT NULL DEFAULT 0,
      new         INTEGER NOT NULL DEFAULT 0,
      detail      TEXT,
      PRIMARY KEY (run_id, idx)
    )""",
]
_ADD_COLUMN = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.IGNORECASE)
```

Set `SCHEMA_VERSION = 3` and append `(3, _V3_STATEMENTS)` to `_STEPS`. In `_apply_step`, replace the `skip_backfill` loop with a generic guard that skips any `ADD COLUMN` whose column already exists:

```python
            skipped_columns = set()
            for statement in statements:
                match = _ADD_COLUMN.match(statement)
                if match:
                    table, column = match.groups()
                    existing = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")]
                    if column in existing:  # e.g. tables built by hand at an older version
                        skipped_columns.add(column)
                        continue
                if statement.startswith("UPDATE jobs SET notified_at") and "notified_at" in skipped_columns:
                    continue
                self.conn.execute(statement)
```

Update the module docstring: version 3, and add `run_queries` to the table list.

Replace `start_run` and add the new methods. `count_jobs` shares its WHERE-building with `list_jobs`, so extract a `_job_where` helper and use it in both:

```python
    def start_run(self, settings: dict, trigger: str = "manual", search_id: int = 1) -> int:
        if trigger not in TRIGGERS:
            raise ValueError(f"Unknown trigger: {trigger}")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs (started_at, state, settings_json, trigger, search_id) "
                "VALUES (?, 'running', ?, ?, ?)",
                (_now(), json.dumps(settings, ensure_ascii=False), trigger, search_id),
            )
        return cur.lastrowid

    def record_query(self, run_id: int, event: dict) -> None:
        """Store one finished query (query_done / query_failed / query_skipped); other events are ignored."""
        status = _QUERY_STATUS.get(event.get("type"))
        if status is None:
            return
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO run_queries "
                "(run_id, idx, source, keyword, location_id, status, found, new, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, event["index"], event["source"], event.get("keyword") or "",
                 event.get("location_id"), status, int(event.get("found") or 0),
                 int(event.get("new") or 0), event.get("error") or event.get("reason")),
            )

    def run_details(self, run_id: int):
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json"))
        item["queries"] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM run_queries WHERE run_id = ? ORDER BY idx", (run_id,))]
        return item

    def last_finished_run(self, trigger=None):
        sql = "SELECT * FROM runs WHERE state != 'running'"
        params = []
        if trigger is not None:
            sql += " AND trigger = ?"
            params.append(trigger)
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json"))
        return item

    def job_counts(self) -> dict:
        counts = {status: 0 for status in STATUSES}
        for status, n in self.conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status"):
            counts[status] = n
        return counts

    def _job_where(self, status, source, run_id, text, include_dismissed):
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
        return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params

    def count_jobs(self, status=None, source=None, run_id=None, text=None, include_dismissed=False) -> int:
        where, params = self._job_where(status, source, run_id, text, include_dismissed)
        return self.conn.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]
```

Change `list_jobs` so that, in place of its own clause-building, it calls `where, params = self._job_where(status, source, run_id, text, include_dismissed)`. Keep its ORDER BY, LIMIT/OFFSET and return value unchanged.

- [ ] **Step 4: Pass the trigger through the pipeline and the terminal**

In `src/pipeline.py`:
- add `trigger="manual"` to the keyword-only parameters of `run_search`, after `delays=None`;
- change line 334 to `run_id = store.start_run({key: list(settings[key]) for key in ("keywords", "locations", "sources")}, trigger=trigger)`;
- add one line to the docstring: "`trigger` is recorded on the run (`manual`, `auto` or `terminal`)."

In `src/main.py`, add `trigger="terminal"` to the `run_search(...)` call.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass. Existing `user_version == 2` assertions in `tests/test_store.py` must change to `SCHEMA_VERSION`. Import `SCHEMA_VERSION` from `store` and assert against it, so a future bump does not break them again. Make that edit and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/store.py src/pipeline.py src/main.py tests/test_store.py tests/test_pipeline.py
git commit -m "Store schema v3: per-query results and run trigger"
```

---

### Task 3: Cross-process run lock (`filelock.py`) used by terminal mode

**Files:**
- Create: `src/filelock.py`, `tests/test_filelock.py`
- Modify: `src/main.py`, `tests/test_main.py`

**Interfaces:**
- Produces (used by Task 6):
  - `filelock.RunLock(path=None)`, with `.acquire() -> bool` (non-blocking), `.release()` and `.held: bool`
  - the default path is `paths.data_dir() / "run.lock"`
- `main.main()` refuses to search while another holder has the lock.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_filelock.py`:

```python
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import filelock
import paths


class RunLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run.lock"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_path_is_in_data_dir(self):
        self.assertEqual(filelock.RunLock().path, paths.data_dir() / "run.lock")

    def test_acquire_and_release(self):
        lock = filelock.RunLock(self.path)
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.held)
        lock.release()
        self.assertFalse(lock.held)
        lock.release()  # harmless twice

    def test_second_holder_is_refused_until_release(self):
        first, second = filelock.RunLock(self.path), filelock.RunLock(self.path)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_lock_held_by_other_process_is_refused(self):
        code = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r})
            import filelock
            lock = filelock.RunLock({str(self.path)!r})
            assert lock.acquire()
            print("locked", flush=True)
            time.sleep(30)
        """)
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            self.assertFalse(filelock.RunLock(self.path).acquire())
        finally:
            child.kill()
            child.wait()
        deadline = time.monotonic() + 5
        lock = filelock.RunLock(self.path)
        while not lock.acquire():  # the OS frees the lock when the process dies
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        lock.release()


if __name__ == "__main__":
    unittest.main()
```

Append to `MainTests` in `tests/test_main.py`:

```python
    def test_main_refuses_when_app_holds_the_run_lock(self):
        import filelock
        holder = filelock.RunLock()
        self.assertTrue(holder.acquire())
        try:
            with patch("main.run_search") as run_search, patch("main.send_email") as send:
                main.main()
            run_search.assert_not_called()
            send.assert_not_called()
            self.assertIn("already running", sys.stdout.getvalue())
        finally:
            holder.release()
```

`MainTests` already patches `sys.stdout` with a `StringIO`, so `sys.stdout.getvalue()` works. If it doesn't, use the patched object the setUp stores.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_filelock tests.test_main -v`
Expected: ERROR `No module named 'filelock'`.

- [ ] **Step 3: Create `src/filelock.py`**

```python
"""
filelock.py — One search at a time, across processes
=====================================================
The desktop app and terminal mode share one data folder. Two searches at once
would double the requests to every site, so both take an exclusive OS lock on
<data>/run.lock before searching. The lock is non-blocking: whoever comes
second is refused. The OS releases it automatically if the holder dies, so a
crash never leaves the user locked out.
"""

import sys
from pathlib import Path

from paths import data_dir


class RunLock:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "run.lock"
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """Take the lock without waiting. False if someone else holds it."""
        if self._handle is not None:
            return True
        handle = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
```

- [ ] **Step 4: Use the lock in `src/main.py`**

Import `from filelock import RunLock`. Right after `store = Store()` and before `try:`, insert:

```python
    run_lock = RunLock()
    if not run_lock.acquire():
        store.close()
        print("[main] A search is already running in the Hawkeye Jobs app — try again when it finishes.")
        return
```

In the existing `finally:` (which closes the store), add `run_lock.release()` before `store.close()`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/filelock.py src/main.py tests/test_filelock.py tests/test_main.py
git commit -m "Add a cross-process run lock shared by the app and terminal mode"
```

---

### Task 4: Shared reporting (`reporting.py`), terminal switched over

**Files:**
- Create: `src/reporting.py`, `tests/test_reporting.py`
- Modify: `src/main.py`

**Interfaces:**
- Produces (used by Task 6):
  - `reporting.REPORT_LIMIT = 500`
  - `reporting.ReportResult(reported: int, more_waiting: bool, delivered: bool, jobs: list)`
  - `reporting.report_pending(store, notifier, limit=REPORT_LIMIT) -> ReportResult`, where `notifier(jobs) -> bool`
- `main.REPORT_LIMIT` stays a module attribute (tests patch it), now re-exported from `reporting`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reporting.py`:

```python
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from reporting import REPORT_LIMIT, report_pending
from store import Store


def job(n):
    return {"title": f"Java Developer {n}", "url": f"https://a.com/{n}", "description": "Acme",
            "source": "Bayt", "location": "jo", "published": ""}


class ReportPendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        run_id = self.store.start_run({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]})
        self.store.add_jobs(run_id, [job(i) for i in range(3)])
        self.calls = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def notifier(self, result):
        def notify(jobs):
            self.calls.append([j["url"] for j in jobs])
            return result
        return notify

    def test_default_limit(self):
        self.assertEqual(REPORT_LIMIT, 500)

    def test_reports_all_pending_and_marks_them(self):
        result = report_pending(self.store, self.notifier(True))
        self.assertEqual((result.reported, result.more_waiting, result.delivered), (3, False, True))
        self.assertEqual(len(self.calls[0]), 3)
        self.assertEqual(self.store.unnotified_jobs(), [])

    def test_failed_delivery_leaves_jobs_pending(self):
        result = report_pending(self.store, self.notifier(False))
        self.assertEqual((result.reported, result.delivered), (0, False))
        self.assertEqual(len(self.store.unnotified_jobs()), 3)

    def test_limit_and_more_waiting(self):
        result = report_pending(self.store, self.notifier(True), limit=2)
        self.assertEqual((result.reported, result.more_waiting), (2, True))
        self.assertEqual(len(self.store.unnotified_jobs()), 1)

    def test_nothing_pending_does_not_call_notifier(self):
        report_pending(self.store, self.notifier(True))
        self.calls.clear()
        result = report_pending(self.store, self.notifier(True))
        self.assertEqual((result.reported, result.jobs), (0, []))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_reporting -v`
Expected: ERROR `No module named 'reporting'`.

- [ ] **Step 3: Create `src/reporting.py`**

```python
"""
reporting.py — Tell the user about new jobs, then mark them reported
=====================================================================
Jobs are saved (and treated as "seen") as each query finishes. Telling the
user happens separately, after the run, so a job is only marked reported once
a notifier confirms it reached the user. Anything not confirmed — a failed
email, a process killed mid-run — is picked up by the next report.

Used by terminal mode (print + optional email) and the desktop app (desktop
notification). A notifier is any callable `notifier(jobs) -> bool`.
"""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

REPORT_LIMIT = 500  # most jobs reported at once; the rest come in the next report


@dataclass
class ReportResult:
    reported: int
    more_waiting: bool
    delivered: bool
    jobs: list = field(default_factory=list)


def report_pending(store, notifier, limit: int = REPORT_LIMIT) -> ReportResult:
    """Report up to `limit` unreported jobs (oldest first); mark them only if `notifier` returns True."""
    fetched = store.unnotified_jobs(limit=limit + 1)
    pending = fetched[:limit]
    more_waiting = len(fetched) > limit
    if not pending:
        return ReportResult(reported=0, more_waiting=False, delivered=True, jobs=[])
    delivered = bool(notifier(pending))
    if delivered:
        store.mark_notified([j["id"] for j in pending])
    else:
        log.info("Report of %d job(s) not delivered; they will be retried next time", len(pending))
    return ReportResult(reported=len(pending) if delivered else 0, more_waiting=more_waiting,
                        delivered=delivered, jobs=pending)
```

- [ ] **Step 4: Switch `src/main.py` to `report_pending`**

- Replace `REPORT_LIMIT = 500  # ...` with `from reporting import REPORT_LIMIT, report_pending` in the imports. The module attribute `main.REPORT_LIMIT` still exists for the tests.
- Replace the block from `fetched = store.unnotified_jobs(...)` through the `else:` branch that marks jobs with:

```python
        def terminal_notifier(jobs) -> bool:
            _print_jobs(jobs)
            if not email_configured():
                return True  # no email set up: the terminal print is the report
            print(f"\n[main] Sending email for {len(jobs)} new jobs...")
            if send_email(jobs):
                return True
            print("[main] Email failed — these jobs will be included again next time.")
            return False

        result = report_pending(store, terminal_notifier, limit=REPORT_LIMIT)
        if not result.jobs:
            print("[main] No new jobs to report")
            return
        if result.more_waiting:
            print("[main] More jobs are waiting — they will come in the next report.")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass, including every existing `tests/test_main.py` reporting test, unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/reporting.py src/main.py tests/test_reporting.py
git commit -m "Move report-then-mark into reporting.py; terminal uses it"
```

---

### Task 5: Desktop notifications (`notify.py`)

**Files:**
- Create: `src/notify.py`, `tests/test_notify.py`

**Interfaces:**
- Produces (used by Tasks 6, 12):
  - `notify.format_message(jobs, language) -> tuple[str, str]`
  - `notify.DesktopNotifier(language_getter, backend=None)`, a callable notifier that always returns `True`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_notify.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import notify


def jobs(n):
    return [{"title": f"Java Developer {i}"} for i in range(n)]


class FakeBackend:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, title, message):
        if self.fail:
            raise RuntimeError("notifications not authorised")
        self.sent.append((title, message))


class FormatTests(unittest.TestCase):
    def test_arabic_message(self):
        title, body = notify.format_message(jobs(5), "ar")
        self.assertEqual(title, "Hawkeye Jobs")
        self.assertTrue(body.startswith("وظائف جديدة: 5"))
        self.assertIn("• Java Developer 0", body)
        self.assertNotIn("Java Developer 3", body)
        self.assertTrue(body.endswith("+2"))

    def test_english_and_unknown_language(self):
        self.assertTrue(notify.format_message(jobs(1), "en")[1].startswith("New jobs: 1"))
        self.assertTrue(notify.format_message(jobs(1), "fr")[1].startswith("New jobs: 1"))


class DesktopNotifierTests(unittest.TestCase):
    def test_sends_in_current_language(self):
        backend = FakeBackend()
        language = ["ar"]
        notifier = notify.DesktopNotifier(lambda: language[0], backend=backend)
        self.assertTrue(notifier(jobs(2)))
        language[0] = "en"
        notifier(jobs(1))
        self.assertTrue(backend.sent[0][1].startswith("وظائف جديدة: 2"))
        self.assertTrue(backend.sent[1][1].startswith("New jobs: 1"))

    def test_backend_failure_still_counts_as_reported(self):
        notifier = notify.DesktopNotifier(lambda: "en", backend=FakeBackend(fail=True))
        with self.assertLogs("notify", "WARNING"):
            self.assertTrue(notifier(jobs(1)))

    def test_no_jobs_sends_nothing(self):
        backend = FakeBackend()
        self.assertTrue(notify.DesktopNotifier(lambda: "en", backend=backend)([]))
        self.assertEqual(backend.sent, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_notify -v`
Expected: ERROR `No module named 'notify'`.

- [ ] **Step 3: Create `src/notify.py`**

```python
"""
notify.py — Desktop notifications for new jobs
===============================================
The app reports new jobs with a native notification (macOS / Windows / Linux,
via desktop-notifier). A notifier always returns True: if the OS refuses or
cannot show notifications, the jobs are still visible as "New" in the app, and
treating them as unreported would make them pile up and re-notify forever.
"""

import logging

log = logging.getLogger(__name__)

APP_NAME = "Hawkeye Jobs"
_HEADLINES = {"ar": "وظائف جديدة: {n}", "en": "New jobs: {n}"}
_SHOWN_TITLES = 3


def format_message(jobs, language) -> tuple:
    template = _HEADLINES.get(language, _HEADLINES["en"])
    lines = [template.format(n=len(jobs))]
    lines += [f"• {job.get('title', '')}" for job in jobs[:_SHOWN_TITLES]]
    if len(jobs) > _SHOWN_TITLES:
        lines.append(f"+{len(jobs) - _SHOWN_TITLES}")
    return APP_NAME, "\n".join(lines)


class DesktopNotifier:
    def __init__(self, language_getter, backend=None):
        self._language = language_getter
        self._backend = backend

    def _get_backend(self):
        if self._backend is None:
            try:
                from desktop_notifier import DesktopNotifierSync
                self._backend = DesktopNotifierSync(app_name=APP_NAME)
            except Exception:
                log.warning("Desktop notifications are unavailable", exc_info=True)
                self._backend = False
        return self._backend

    def __call__(self, jobs) -> bool:
        if not jobs:
            return True
        title, message = format_message(jobs, self._language())
        backend = self._get_backend()
        if backend:
            try:
                backend.send(title=title, message=message)
            except Exception:
                log.warning("Could not show a desktop notification", exc_info=True)
        return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/notify.py tests/test_notify.py
git commit -m "Add desktop notifications for new jobs"
```

---

### Task 6: `RunManager` (single run, events, recording, reporting)

**Files:**
- Create: `src/runner.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes:
  - `filelock.RunLock` (Task 3)
  - `reporting.report_pending` (Task 4)
  - `Store.record_query` and `Store.last_finished_run` (Task 2)
  - `settings.load_config`, `load_app_settings`, `save_app_settings` (Task 1)
  - `pipeline.run_search(..., trigger=)` (Task 2)
- Produces (used by Tasks 7, 9–12):
  - `runner.RunInProgress(RuntimeError)`
  - `runner.NoSearchSettings(RuntimeError)`
  - `runner.POSTS_INTERVAL_SECONDS = 86400`
  - `runner.RunManager(*, notifier, run_search=None, store_factory=Store, lock=None, settings_loader=load_config, app_settings_loader=load_app_settings, app_settings_saver=save_app_settings, country_labels_loader=None, clock=time.time)`, with:
    - `.start(trigger="manual") -> int | None`: the run id; raises `RunInProgress` or `NoSearchSettings`
    - `.cancel() -> bool`
    - `.is_running: bool`
    - `.current -> dict | None`: `{"run_id", "trigger", "total", "done", "found", "new"}`
    - `.wait(timeout) -> bool`
    - `.subscribe() -> queue.Queue`, `.unsubscribe(q)`, `.is_subscribed(q) -> bool`
    - `.close_subscribers()`: puts `None` in every queue
    - `.report_pending_now() -> int`
- Events published to subscribers:
  - every pipeline event plus `"trigger"`;
  - `{"type": "posts_skipped", "reason": "daily_limit"}`;
  - `{"type": "jobs_reported", "count": n, "more_waiting": bool}`;
  - `{"type": "run_error"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner.py`:

```python
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import filelock
from pipeline import RunSummary
from runner import NoSearchSettings, RunInProgress, RunManager
from store import Store

SETTINGS = {"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt", "linkedin_posts"]}


def job(n):
    return {"title": f"Java Developer {n}", "url": f"https://a.com/{n}", "description": "Acme",
            "source": "Bayt", "location": "jo", "published": ""}


class FakeSearch:
    """Stands in for pipeline.run_search: emits a short run and saves jobs like the real one."""

    def __init__(self, hold=None):
        self.calls = []
        self.hold = hold  # threading.Event: block mid-run until set

    def __call__(self, settings, on_event=None, cancel=None, *, country_labels=None, store=None, trigger="manual",
                 **kwargs):
        self.calls.append({"settings": settings, "trigger": trigger})
        run_id = store.start_run(settings, trigger=trigger)
        on_event({"type": "run_started", "run_id": run_id, "total": 2})
        q = {"index": 0, "source": "bayt", "keyword": "Java", "location_id": "jo"}
        on_event({"type": "query_started", **q})
        if self.hold is not None:
            while not self.hold.is_set() and not cancel.is_set():
                time.sleep(0.01)
        new = store.add_jobs(run_id, [job(1), job(2)])
        on_event({"type": "query_done", **q, "found": 2, "new": len(new)})
        if "linkedin_posts" in settings["sources"]:
            p = {"index": 1, "source": "linkedin_posts", "keyword": "Java", "location_id": None}
            on_event({"type": "query_started", **p})
            on_event({"type": "query_done", **p, "found": 0, "new": 0})
        state = "cancelled" if cancel.is_set() else "done"
        store.finish_run(run_id, state, 2, len(new))
        on_event({"type": "run_finished", "run_id": run_id, "state": state, "found": 2, "new": len(new)})
        return RunSummary(run_id=run_id, state=state, found=2, new_jobs=new)


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.lock_path = Path(self.tmp.name) / "run.lock"
        self.app = {"last_posts_run": None, "language": "en"}
        self.saved_app = []
        self.notified = []
        self.search = FakeSearch()
        self.settings = dict(SETTINGS)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kwargs):
        def saver(patch):
            self.saved_app.append(patch)
            self.app.update(patch)
            return dict(self.app)
        defaults = dict(
            notifier=lambda jobs: self.notified.append(len(jobs)) or True,
            run_search=self.search,
            store_factory=lambda: Store(self.db),
            lock=filelock.RunLock(self.lock_path),
            settings_loader=lambda: self.settings,
            app_settings_loader=lambda: dict(self.app),
            app_settings_saver=saver,
            country_labels_loader=lambda: {"jo": "Jordan"},
        )
        defaults.update(kwargs)
        return RunManager(**defaults)

    def drain(self, q):
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return events


class StartTests(RunnerTestCase):
    def test_run_records_queries_reports_and_publishes(self):
        manager = self.make()
        q = manager.subscribe()
        run_id = manager.start("manual")
        self.assertTrue(manager.wait(5))
        self.assertIsNotNone(run_id)
        types = [e["type"] for e in self.drain(q)]
        self.assertEqual(types[0], "run_started")
        self.assertIn("run_finished", types)
        self.assertEqual(types[-1], "jobs_reported")
        with Store(self.db) as store:
            details = store.run_details(run_id)
            self.assertEqual([x["source"] for x in details["queries"]], ["bayt", "linkedin_posts"])
            self.assertEqual(store.unnotified_jobs(), [])
        self.assertEqual(self.notified, [2])
        self.assertFalse(manager.is_running)
        self.assertIsNone(manager.current)

    def test_events_carry_the_trigger_and_current_progress(self):
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        q = manager.subscribe()
        manager.start("auto")
        deadline = time.monotonic() + 5
        while manager.current is None or manager.current["total"] != 2:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(manager.current["trigger"], "auto")
        hold.set()
        manager.wait(5)
        self.assertTrue(all(e.get("trigger") == "auto" for e in self.drain(q) if e["type"].startswith(("run_", "query_"))))
        self.assertEqual(self.search.calls[0]["trigger"], "auto")

    def test_second_start_while_running_is_refused(self):
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        manager.start()
        with self.assertRaises(RunInProgress):
            manager.start()
        hold.set()
        manager.wait(5)

    def test_start_refused_when_file_lock_held_elsewhere(self):
        other = filelock.RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        try:
            with self.assertRaises(RunInProgress):
                self.make().start()
            self.assertEqual(self.search.calls, [])
        finally:
            other.release()

    def test_lock_released_after_run(self):
        manager = self.make()
        manager.start()
        manager.wait(5)
        other = filelock.RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        other.release()

    def test_no_settings_is_refused(self):
        self.settings = None
        with self.assertRaises(NoSearchSettings):
            self.make().start()
        other = filelock.RunLock(self.lock_path)
        self.assertTrue(other.acquire())  # lock was not kept
        other.release()

    def test_cancel(self):
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        self.assertFalse(manager.cancel())
        manager.start()
        self.assertTrue(manager.cancel())
        self.assertTrue(manager.wait(5))
        with Store(self.db) as store:
            self.assertEqual(store.list_runs()[0]["state"], "cancelled")

    def test_crash_in_search_publishes_run_error_and_releases(self):
        def boom(*args, **kwargs):
            raise RuntimeError("disk full")
        manager = self.make(run_search=boom)
        q = manager.subscribe()
        with self.assertLogs("runner", "ERROR"):
            manager.start()
            manager.wait(5)
        self.assertIn("run_error", [e["type"] for e in self.drain(q)])
        self.assertFalse(manager.is_running)


class PostsGateTests(RunnerTestCase):
    def test_posts_run_marks_last_posts_run(self):
        manager = self.make(clock=lambda: 1_000_000.0)
        manager.start()
        manager.wait(5)
        self.assertEqual(self.saved_app, [{"last_posts_run": "1970-01-12T13:46:40Z"}])

    def test_posts_skipped_within_24h(self):
        self.app["last_posts_run"] = "1970-01-12T13:46:40Z"
        manager = self.make(clock=lambda: 1_000_000.0 + 3600)
        q = manager.subscribe()
        manager.start()
        manager.wait(5)
        self.assertNotIn("linkedin_posts", self.search.calls[0]["settings"]["sources"])
        self.assertIn({"type": "posts_skipped", "reason": "daily_limit"}, self.drain(q))

    def test_posts_allowed_after_24h(self):
        self.app["last_posts_run"] = "1970-01-12T13:46:40Z"
        manager = self.make(clock=lambda: 1_000_000.0 + 86400)
        manager.start()
        manager.wait(5)
        self.assertIn("linkedin_posts", self.search.calls[0]["settings"]["sources"])


class SubscriberTests(RunnerTestCase):
    def test_slow_subscriber_is_dropped_others_keep_receiving(self):
        manager = self.make()
        slow = manager.subscribe(maxsize=1)
        fast = manager.subscribe()
        manager.start()
        manager.wait(5)
        self.assertFalse(manager.is_subscribed(slow))
        self.assertTrue(manager.is_subscribed(fast))
        self.assertGreater(len(self.drain(fast)), 3)

    def test_close_subscribers_sends_sentinel(self):
        manager = self.make()
        q = manager.subscribe()
        manager.close_subscribers()
        self.assertIsNone(q.get_nowait())
        self.assertFalse(manager.is_subscribed(q))


class ReportNowTests(RunnerTestCase):
    def test_report_pending_now_notifies_leftovers(self):
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
            store.add_jobs(run_id, [job(7)])  # saved by a run that was killed before reporting
        manager = self.make()
        q = manager.subscribe()
        self.assertEqual(manager.report_pending_now(), 1)
        self.assertEqual(self.notified, [1])
        self.assertEqual(self.drain(q), [{"type": "jobs_reported", "count": 1, "more_waiting": False}])

    def test_report_pending_now_skipped_while_running(self):
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        manager.start()
        self.assertEqual(manager.report_pending_now(), 0)
        hold.set()
        manager.wait(5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_runner -v`
Expected: ERROR `No module named 'runner'`.

- [ ] **Step 3: Create `src/runner.py`**

```python
"""
runner.py — The only way the desktop app starts a search
=========================================================
RunManager guarantees one search at a time — an in-process lock for the app's
own threads plus filelock.RunLock shared with terminal mode — and runs
pipeline.run_search on a worker thread. While it runs it:
  * records each finished query in run_queries (History page),
  * keeps a small `current` progress snapshot for /api/state,
  * fans every event out to SSE subscribers (bounded queues; a subscriber
    that falls behind is dropped and the UI reconnects),
  * enforces "LinkedIn posts at most once per 24 h" (the user's own account).
After the run it reports new jobs through the notifier (reporting.py).
AutoSearch (below) starts runs on a timer through the same RunManager.
"""

import logging
import queue
import threading
import time
from datetime import datetime, timezone

from filelock import RunLock
from reporting import report_pending
from settings import load_app_settings, load_config, save_app_settings
from store import Store

log = logging.getLogger(__name__)

POSTS_SOURCE = "linkedin_posts"
POSTS_INTERVAL_SECONDS = 24 * 3600
_SUBSCRIBER_QUEUE_SIZE = 1000
_FINISHED_QUERY = ("query_done", "query_failed", "query_skipped")


class RunInProgress(RuntimeError):
    """A search is already running (in this app or in terminal mode)."""


class NoSearchSettings(RuntimeError):
    """The user has not set up a search yet."""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _default_country_labels() -> dict:
    from countries import CountryFetchError, load_countries
    try:
        return {c["id"]: c["label"] for c in load_countries()}
    except CountryFetchError:
        return {}


class RunManager:
    def __init__(self, *, notifier, run_search=None, store_factory=Store, lock=None,
                 settings_loader=load_config, app_settings_loader=load_app_settings,
                 app_settings_saver=save_app_settings, country_labels_loader=None, clock=time.time):
        if run_search is None:
            from pipeline import run_search
        self._notifier = notifier
        self._run_search = run_search
        self._store_factory = store_factory
        self._run_lock = lock or RunLock()
        self._settings_loader = settings_loader
        self._app_settings_loader = app_settings_loader
        self._app_settings_saver = app_settings_saver
        self._country_labels_loader = country_labels_loader or _default_country_labels
        self._clock = clock
        self._state_lock = threading.Lock()
        self._subscribers_lock = threading.Lock()
        self._subscribers = set()
        self._worker = None
        self._cancel = None
        self._current = None

    # ── subscribers ─────────────────────────────────────────────────────

    def subscribe(self, maxsize: int = _SUBSCRIBER_QUEUE_SIZE) -> queue.Queue:
        q = queue.Queue(maxsize=maxsize)
        with self._subscribers_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._subscribers_lock:
            self._subscribers.discard(q)

    def is_subscribed(self, q) -> bool:
        with self._subscribers_lock:
            return q in self._subscribers

    def close_subscribers(self) -> None:
        with self._subscribers_lock:
            subscribers, self._subscribers = self._subscribers, set()
        for q in subscribers:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def _publish(self, event: dict) -> None:
        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                log.info("Dropping a slow event subscriber")
                self.unsubscribe(q)

    # ── state ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._worker is not None and self._worker.is_alive()

    @property
    def current(self):
        with self._state_lock:
            return dict(self._current) if self._current else None

    def wait(self, timeout=None) -> bool:
        worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def cancel(self) -> bool:
        with self._state_lock:
            if self._worker is None or not self._worker.is_alive():
                return False
            self._cancel.set()
            return True

    # ── running ─────────────────────────────────────────────────────────

    def start(self, trigger: str = "manual"):
        """Start a search in the background and return its run id (None if it failed before starting)."""
        with self._state_lock:
            if self._worker is not None and self._worker.is_alive():
                raise RunInProgress("a search is already running in the app")
            if not self._run_lock.acquire():
                raise RunInProgress("a search is already running in another window or the terminal")
            settings = self._settings_loader()
            if not settings:
                self._run_lock.release()
                raise NoSearchSettings("no search settings saved")
            settings, posts_skipped = self._apply_posts_gate(dict(settings))
            self._cancel = threading.Event()
            started = threading.Event()
            holder = {}
            self._worker = threading.Thread(target=self._run, args=(settings, trigger, started, holder),
                                            name="hawkeye-run", daemon=True)
            self._worker.start()
        if posts_skipped:
            self._publish({"type": "posts_skipped", "reason": "daily_limit"})
        started.wait(timeout=10)
        return holder.get("run_id")

    def _apply_posts_gate(self, settings: dict):
        if POSTS_SOURCE not in settings.get("sources", []):
            return settings, False
        last = _parse_iso(self._app_settings_loader().get("last_posts_run"))
        if last is not None and self._clock() - last < POSTS_INTERVAL_SECONDS:
            settings["sources"] = [s for s in settings["sources"] if s != POSTS_SOURCE]
            return settings, True
        return settings, False

    def _run(self, settings, trigger, started, holder) -> None:
        store = None
        posts_marked = False
        try:
            store = self._store_factory()
            labels = self._country_labels_loader()

            def on_event(event: dict) -> None:
                nonlocal posts_marked
                kind = event["type"]
                if kind == "run_started":
                    holder["run_id"] = event["run_id"]
                    with self._state_lock:
                        self._current = {"run_id": event["run_id"], "trigger": trigger, "total": event["total"],
                                         "done": 0, "found": 0, "new": 0}
                    started.set()
                elif kind in _FINISHED_QUERY:
                    store.record_query(holder["run_id"], event)
                    with self._state_lock:
                        if self._current:
                            self._current["done"] += 1
                            self._current["found"] += int(event.get("found") or 0)
                            self._current["new"] += int(event.get("new") or 0)
                elif kind == "query_started" and event.get("source") == POSTS_SOURCE and not posts_marked:
                    posts_marked = True
                    self._app_settings_saver({"last_posts_run": _iso(self._clock())})
                self._publish({**event, "trigger": trigger})

            self._run_search(settings, on_event=on_event, cancel=self._cancel, country_labels=labels,
                             store=store, trigger=trigger)
            result = report_pending(store, self._notifier)
            self._publish({"type": "jobs_reported", "count": result.reported, "more_waiting": result.more_waiting})
        except Exception:
            log.exception("Search run crashed")
            self._publish({"type": "run_error"})
        finally:
            started.set()
            if store is not None:
                store.close()
            with self._state_lock:
                self._current = None
            self._run_lock.release()

    def report_pending_now(self) -> int:
        """Report jobs left unreported (e.g. the app was killed mid-run). Skipped while a search runs."""
        if self.is_running:
            return 0
        with self._store_factory() as store:
            result = report_pending(store, self._notifier)
        if result.jobs:
            self._publish({"type": "jobs_reported", "count": result.reported, "more_waiting": result.more_waiting})
        return result.reported
```

`started.wait` happens outside `_state_lock`, so the worker can take `_state_lock` in `on_event`.

- [ ] **Step 4: Run the tests to verify they pass, five times**

Run: `for i in 1 2 3 4 5; do venv/bin/python3 -m unittest tests.test_runner 2>&1 | tail -1; done`
Expected: `OK` five times.

- [ ] **Step 5: Full suite and commit**

Run: `venv/bin/python3 -m unittest discover -s tests -v`. Expected: all pass.

```bash
git add src/runner.py tests/test_runner.py
git commit -m "Add RunManager: one search at a time, live events, per-query history, reporting"
```

---

### Task 7: Auto-search scheduler (`runner.AutoSearch`)

**Files:**
- Modify: `src/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces (used by Task 12):
  - `runner.AutoSearch(manager, *, app_settings_loader=load_app_settings, store_factory=Store, clock=time.time, interval=60)`, with:
    - `.due() -> bool`
    - `.tick() -> bool`: True if it started a run
    - `.start()`, `.stop()`
- **Due** means:
  - `auto_search.enabled` and `onboarding_done` are both set;
  - at least one run has finished (the user's first search is manual);
  - `now - finished_at(last finished run) >= hours * 3600`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runner.py` (above the `__main__` guard), and add `from runner import AutoSearch` to the imports:

```python
class AutoSearchTests(RunnerTestCase):
    def finished_run_at(self, iso):
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
            store.finish_run(run_id, "done", 0, 0)
            store.conn.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (iso, run_id))
            store.conn.commit()

    def auto(self, manager, now, enabled=True, hours=6, onboarded=True):
        app = {"auto_search": {"enabled": enabled, "hours": hours}, "onboarding_done": onboarded}
        return AutoSearch(manager, app_settings_loader=lambda: app, store_factory=lambda: Store(self.db),
                          clock=lambda: now, interval=0.05)

    def test_not_due_without_any_finished_run(self):
        self.assertFalse(self.auto(self.make(), now=10_000_000).due())

    def test_due_after_interval(self):
        self.finished_run_at("1970-04-26T17:46:40Z")  # 10_000_000
        manager = self.make()
        self.assertFalse(self.auto(manager, now=10_000_000 + 6 * 3600 - 1).due())
        self.assertTrue(self.auto(manager, now=10_000_000 + 6 * 3600).due())

    def test_disabled_or_not_onboarded_is_never_due(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        manager = self.make()
        self.assertFalse(self.auto(manager, now=20_000_000, enabled=False).due())
        self.assertFalse(self.auto(manager, now=20_000_000, onboarded=False).due())

    def test_tick_starts_auto_run(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        manager = self.make()
        self.assertTrue(self.auto(manager, now=20_000_000).tick())
        manager.wait(5)
        self.assertEqual(self.search.calls[0]["trigger"], "auto")

    def test_tick_skips_while_running(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        manager.start()
        self.assertFalse(self.auto(manager, now=20_000_000).tick())
        hold.set()
        manager.wait(5)
        self.assertEqual(len(self.search.calls), 1)

    def test_tick_skips_when_terminal_holds_lock(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        other = filelock.RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        try:
            self.assertFalse(self.auto(self.make(), now=20_000_000).tick())
        finally:
            other.release()

    def test_background_thread_ticks_and_stops(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        manager = self.make()
        auto = self.auto(manager, now=20_000_000)
        auto.start()
        deadline = time.monotonic() + 5
        while not self.search.calls:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        auto.stop()
        manager.wait(5)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_runner -v`
Expected: ERROR `cannot import name 'AutoSearch'`.

- [ ] **Step 3: Add `AutoSearch` to `src/runner.py`**

```python
class AutoSearch:
    """Starts a search every N hours while the app is open, through the same RunManager."""

    def __init__(self, manager, *, app_settings_loader=load_app_settings, store_factory=Store,
                 clock=time.time, interval: float = 60):
        self._manager = manager
        self._app_settings_loader = app_settings_loader
        self._store_factory = store_factory
        self._clock = clock
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None

    def due(self) -> bool:
        app = self._app_settings_loader()
        auto = app.get("auto_search") or {}
        if not auto.get("enabled") or not app.get("onboarding_done"):
            return False
        with self._store_factory() as store:
            last = store.last_finished_run()
        finished = _parse_iso(last.get("finished_at")) if last else None
        if finished is None:
            return False
        return self._clock() - finished >= auto["hours"] * 3600

    def tick(self) -> bool:
        if self._manager.is_running:
            return False
        try:
            if not self.due():
                return False
            self._manager.start("auto")
            return True
        except (RunInProgress, NoSearchSettings):
            return False
        except Exception:
            log.exception("Auto-search tick failed")
            return False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hawkeye-auto-search", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.tick()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
```

Also extend the module docstring line about AutoSearch: "ticks every 60 s; due when enabled, onboarding is done, and `hours` have passed since the last finished run of any kind."

- [ ] **Step 4: Run the tests five times, then the full suite**

Run: `for i in 1 2 3 4 5; do venv/bin/python3 -m unittest tests.test_runner 2>&1 | tail -1; done`, then `venv/bin/python3 -m unittest discover -s tests -v`
Expected: `OK` every time.

- [ ] **Step 5: Commit**

```bash
git add src/runner.py tests/test_runner.py
git commit -m "Add auto-search scheduler on top of RunManager"
```

---

### Task 8: Localized country list for the UI

**Files:**
- Modify: `src/countries.py`
- Test: `tests/test_countries.py`

**Interfaces:**
- Produces (used by Task 9):
  - `countries.offline_countries() -> list[dict]`: Remote plus the countries in `catalog.OVERRIDES`, with English labels; no network
  - `countries.localized_countries(language: str, base: list[dict]) -> list[dict]`: `[{"id", "label"}]`, Remote first, labels from Babel CLDR for `language` with the base label as fallback, the rest sorted by label

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_countries.py` (above the `__main__` guard):

```python
from countries import localized_countries, offline_countries


class LocalizedCountriesTests(unittest.TestCase):
    BASE = [{"id": "remote", "label": "Remote"}, {"id": "jo", "label": "Jordan"},
            {"id": "sa", "label": "Saudi Arabia"}, {"id": "zz", "label": "Zedland"}]

    def test_arabic_labels_with_remote_first(self):
        result = localized_countries("ar", self.BASE)
        self.assertEqual(result[0], {"id": "remote", "label": "عن بُعد"})
        labels = {c["id"]: c["label"] for c in result}
        self.assertEqual(labels["jo"], "الأردن")
        self.assertEqual(labels["sa"], "المملكة العربية السعودية")
        self.assertEqual(labels["zz"], "Zedland")  # unknown to CLDR: keep the base label

    def test_english_sorted(self):
        result = localized_countries("en", self.BASE)
        self.assertEqual([c["id"] for c in result], ["remote", "jo", "sa", "zz"])
        self.assertEqual(result[0]["label"], "Remote")

    def test_offline_list_has_remote_and_overrides(self):
        ids = [c["id"] for c in offline_countries()]
        self.assertEqual(ids[0], "remote")
        self.assertIn("jo", ids)
        self.assertIn("ae", ids)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_countries -v`
Expected: ImportError for `localized_countries`.

- [ ] **Step 3: Add to `src/countries.py`**

```python
_REMOTE_LABELS = {"ar": "عن بُعد", "en": "Remote"}


def offline_countries() -> list:
    """Remote + the countries we have site-specific mappings for — used when the country API is unreachable."""
    from catalog import OVERRIDES
    rows = [{"id": cid, "label": spec["label"]} for cid, spec in OVERRIDES.items()]
    rows.sort(key=lambda c: c["label"].casefold())
    return _with_remote(rows)


def localized_countries(language: str, base: list) -> list:
    """[{id, label}] in the UI language (Babel/CLDR, offline), Remote first, the rest sorted by label."""
    try:
        from babel import Locale
        territories = Locale.parse(language).territories
    except Exception:
        territories = {}
    rest = [{"id": c["id"], "label": territories.get(c["id"].upper(), c["label"])}
            for c in base if c["id"] != "remote"]
    rest.sort(key=lambda c: c["label"].casefold())
    return [{"id": "remote", "label": _REMOTE_LABELS.get(language, "Remote")}] + rest
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/countries.py tests/test_countries.py
git commit -m "Localized country names for the UI (Babel, offline)"
```

---

### Task 9: Local web server (`web/server.py`) and read-only API

**Files:**
- Create: `src/web/__init__.py` (empty), `src/web/server.py`, `src/web/api.py`, `src/web/services.py`, `src/web/static/index.html` (placeholder), `tests/test_api.py`

**Interfaces:**
- Consumes:
  - `RunManager` (Task 6)
  - Store methods (Task 2)
  - settings functions (Task 1)
  - `localized_countries` and `offline_countries` (Task 8)
  - `browser.find_chrome`
  - `paths.linkedin_profile_dir`
- Produces (used by Tasks 10–12):
  - `web.services.AppServices`, a dataclass with fields:
    `manager, store_factory, load_config, save_config, load_app_settings, save_app_settings, base_countries, find_chrome, linkedin_profile_dir, data_dir, log_path, open_url, open_path, launch, fetch_latest_release, version, clock, os_name, home, user`
  - `AppServices.default(manager)`
  - `web.server.create_app(*, token: str, allowed_hosts: set[str], services: AppServices) -> FastAPI`
  - Endpoints in this task:
    - `GET /api/state`
    - `GET /api/settings`
    - `GET /api/app-settings`
    - `GET /api/countries?lang=`
    - `GET /api/jobs`
    - `GET /api/runs`
    - `GET /api/runs/{id}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api.py`:

```python
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from fastapi.testclient import TestClient

from store import Store
from web.server import create_app
from web.services import AppServices

TOKEN = "t" * 43
HOST = "127.0.0.1:5555"


class FakeManager:
    def __init__(self):
        self.is_running = False
        self.current = None
        self.started = []
        self.cancelled = 0
        self.queue = None
        self.subscribed = True

    def start(self, trigger="manual"):
        from runner import NoSearchSettings, RunInProgress
        if self.is_running:
            raise RunInProgress("busy")
        if getattr(self, "no_settings", False):
            raise NoSearchSettings("none")
        self.started.append(trigger)
        return 42

    def cancel(self):
        self.cancelled += 1
        return self.is_running

    def subscribe(self, maxsize=1000):
        import queue
        self.queue = queue.Queue()
        return self.queue

    def unsubscribe(self, q):
        self.subscribed = False

    def is_subscribed(self, q):
        return self.subscribed


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "t.db"
        self.config = {"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}
        self.app_settings = {"language": "ar", "theme": "auto", "auto_search": {"enabled": True, "hours": 6},
                             "onboarding_done": True, "update_check": True, "last_update_check": None,
                             "latest_version": None, "last_posts_run": None}
        self.opened_urls, self.opened_paths, self.launched = [], [], []
        self.release = {"tag_name": "v1.0.0"}
        self.manager = FakeManager()
        (root / "profile").mkdir()

        def save_app(patch):
            import settings
            errors = {k: "unknown" for k in patch if k not in self.app_settings}
            if errors:
                raise settings.SettingsError(errors)
            self.app_settings.update(patch)
            return dict(self.app_settings)

        self.services = AppServices(
            manager=self.manager,
            store_factory=lambda: Store(self.db),
            load_config=lambda: self.config,
            save_config=lambda data: self.config.update(data),
            load_app_settings=lambda: dict(self.app_settings),
            save_app_settings=save_app,
            base_countries=lambda: [{"id": "remote", "label": "Remote"}, {"id": "jo", "label": "Jordan"},
                                    {"id": "sa", "label": "Saudi Arabia"}],
            find_chrome=lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            linkedin_profile_dir=lambda: root / "profile",
            data_dir=lambda: root,
            log_path=lambda: root / "hawkeye.log",
            open_url=self.opened_urls.append,
            open_path=self.opened_paths.append,
            launch=self.launched.append,
            fetch_latest_release=lambda: self.release,
            version="1.0.0",
            clock=lambda: 1_000_000.0,
            os_name="macOS 15",
            home=str(root / "home" / "sam"),
            user="sam",
        )
        self.client = TestClient(create_app(token=TOKEN, allowed_hosts={HOST}, services=self.services),
                                 base_url=f"http://{HOST}")
        with Store(self.db) as store:
            run_id = store.start_run(self.config)
            store.add_jobs(run_id, [{"title": "Java Developer", "url": "https://a.com/1", "description": "Acme",
                                     "source": "Bayt", "location": "jo", "published": ""}])
            store.record_query(run_id, {"type": "query_done", "index": 0, "source": "bayt", "keyword": "Java",
                                        "location_id": "jo", "found": 1, "new": 1})
            store.finish_run(run_id, "done", 1, 1)
            self.run_id = run_id

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, path, **kwargs):
        return self.client.get(path, headers={"X-Hawkeye-Token": TOKEN}, **kwargs)


class GuardTests(ApiTestCase):
    def test_api_without_token_is_forbidden(self):
        self.assertEqual(self.client.get("/api/state").status_code, 403)
        self.assertEqual(self.client.get("/api/state", headers={"X-Hawkeye-Token": "wrong"}).status_code, 403)

    def test_foreign_host_is_forbidden_even_for_static(self):
        evil = TestClient(create_app(token=TOKEN, allowed_hosts={HOST}, services=self.services),
                          base_url="http://evil.example:5555")
        self.assertEqual(evil.get("/").status_code, 403)
        self.assertEqual(evil.get("/api/state", headers={"X-Hawkeye-Token": TOKEN}).status_code, 403)

    def test_token_query_param_only_for_events(self):
        self.assertEqual(self.client.get(f"/api/state?token={TOKEN}").status_code, 403)

    def test_static_index_served_without_token(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Hawkeye Jobs", response.text)


class ReadTests(ApiTestCase):
    def test_state(self):
        state = self.get("/api/state").json()
        self.assertEqual(state["version"], "1.0.0")
        self.assertTrue(state["onboarding_done"])
        self.assertEqual(state["language"], "ar")
        self.assertTrue(state["chrome"]["found"])
        self.assertFalse(state["linkedin_profile"])  # folder exists but is empty
        self.assertIsNone(state["run"])
        self.assertEqual(state["job_counts"]["new"], 1)
        self.assertTrue(state["has_settings"])
        self.assertEqual(state["update"], {"available": False, "latest": None})

    def test_settings_and_app_settings(self):
        self.assertEqual(self.get("/api/settings").json(), self.config)
        self.assertEqual(self.get("/api/app-settings").json()["language"], "ar")

    def test_settings_when_none_saved(self):
        self.config = None
        self.services.load_config = lambda: None
        self.assertEqual(self.get("/api/settings").json(), {"keywords": [], "locations": [], "sources": []})

    def test_countries_localized(self):
        rows = self.get("/api/countries?lang=ar").json()
        self.assertEqual(rows[0], {"id": "remote", "label": "عن بُعد"})
        self.assertIn({"id": "jo", "label": "الأردن"}, rows)
        self.assertEqual(self.get("/api/countries?lang=xx").status_code, 422)

    def test_jobs_list_and_filters(self):
        body = self.get("/api/jobs").json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["jobs"][0]["title"], "Java Developer")
        self.assertEqual(self.get("/api/jobs?status=saved").json()["total"], 0)
        self.assertEqual(self.get("/api/jobs?status=bogus").status_code, 422)
        self.assertEqual(self.get("/api/jobs?limit=500").status_code, 422)

    def test_runs(self):
        runs = self.get("/api/runs").json()["runs"]
        self.assertEqual(runs[0]["id"], self.run_id)
        details = self.get(f"/api/runs/{self.run_id}").json()
        self.assertEqual(details["queries"][0]["status"], "done")
        self.assertEqual(self.get("/api/runs/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_api -v`
Expected: ERROR `No module named 'web'`.

- [ ] **Step 3: Create `src/web/services.py`**

```python
"""
services.py — Everything the local API needs from the outside world
====================================================================
The API handlers only talk to an AppServices object, so tests can swap in
fakes (no network, no Chrome, no real folders) and app.py wires the real ones.
"""

import getpass
import json
import logging
import os
import platform
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/Abdallahabusnineh/hawkeye-jobs/releases/latest"


def _open_path(path) -> None:
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — opening the user's own data folder
    else:
        subprocess.Popen(["xdg-open", path])


def _launch(argv) -> None:
    subprocess.Popen(list(argv), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _fetch_latest_release() -> dict:
    import requests
    response = requests.get(RELEASES_API, timeout=5,
                            headers={"Accept": "application/vnd.github+json", "User-Agent": "hawkeye-jobs"})
    response.raise_for_status()
    return response.json()


def _base_countries_factory():
    cache = {}

    def base_countries():
        if "rows" not in cache:
            from countries import CountryFetchError, load_countries, offline_countries
            try:
                cache["rows"] = load_countries()
            except CountryFetchError:
                cache["rows"] = offline_countries()
        return cache["rows"]
    return base_countries


@dataclass
class AppServices:
    manager: object
    store_factory: Callable
    load_config: Callable
    save_config: Callable
    load_app_settings: Callable
    save_app_settings: Callable
    base_countries: Callable
    find_chrome: Callable
    linkedin_profile_dir: Callable
    data_dir: Callable
    log_path: Callable
    open_url: Callable
    open_path: Callable
    launch: Callable
    fetch_latest_release: Callable
    version: str
    clock: Callable = time.time
    os_name: str = field(default_factory=lambda: f"{platform.system()} {platform.release()}")
    home: str = field(default_factory=lambda: str(Path.home()))
    user: str = field(default_factory=getpass.getuser)

    @classmethod
    def default(cls, manager) -> "AppServices":
        import paths
        import settings
        from browser import find_chrome
        from store import Store
        from version import __version__
        return cls(
            manager=manager,
            store_factory=Store,
            load_config=settings.load_config,
            save_config=settings.save_config,
            load_app_settings=settings.load_app_settings,
            save_app_settings=settings.save_app_settings,
            base_countries=_base_countries_factory(),
            find_chrome=find_chrome,
            linkedin_profile_dir=paths.linkedin_profile_dir,
            data_dir=paths.data_dir,
            log_path=paths.log_path,
            open_url=webbrowser.open,
            open_path=_open_path,
            launch=_launch,
            fetch_latest_release=_fetch_latest_release,
            version=__version__,
        )
```

- [ ] **Step 4: Create `src/web/server.py`**

```python
"""
server.py — The app's local web server (127.0.0.1 only)
========================================================
The window (pywebview) talks to this FastAPI app on the user's own machine.
Nothing here is reachable from other computers. Two guards keep other web
pages in the user's browser out:
  * Host must be 127.0.0.1:<port> / localhost:<port> (blocks DNS rebinding);
  * every /api/* call must carry the per-launch random token
    (X-Hawkeye-Token header, or ?token= on GET /api/events only, because
    EventSource cannot set headers).
Static files (the UI) are served without the token; they contain no data.
"""

import hmac
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from web.api import router

STATIC_DIR = Path(__file__).resolve().parent / "static"
EVENTS_PATH = "/api/events"


def create_app(*, token: str, allowed_hosts: set, services, static_dir: Path = STATIC_DIR) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-hawkeye-token")
            if supplied is None and request.url.path == EVENTS_PATH:
                supplied = request.query_params.get("token")
            if not supplied or not hmac.compare_digest(supplied, token):
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    app.include_router(router, prefix="/api")
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app
```

- [ ] **Step 5: Create `src/web/api.py` with the read endpoints**

```python
"""
api.py — Endpoints the UI calls (all local; see server.py for the guards)
==========================================================================
Responses are data and codes only — the UI owns every displayed word.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from countries import localized_countries
from settings import LANGUAGES
from store import STATUSES

log = logging.getLogger(__name__)
router = APIRouter()

EMPTY_SETTINGS = {"keywords": [], "locations": [], "sources": []}


def _services(request: Request):
    return request.app.state.services


def _version_tuple(text):
    try:
        return tuple(int(part) for part in str(text).lstrip("vV").split("."))
    except ValueError:
        return ()


@router.get("/state")
def get_state(request: Request):
    s = _services(request)
    app = s.load_app_settings()
    profile = s.linkedin_profile_dir()
    with s.store_factory() as store:
        counts = store.job_counts()
    latest = app.get("latest_version")
    return {
        "version": s.version,
        "onboarding_done": app["onboarding_done"],
        "language": app["language"],
        "theme": app["theme"],
        "chrome": {"found": bool(s.find_chrome())},
        "linkedin_profile": profile.exists() and any(profile.iterdir()),
        "run": s.manager.current,
        "job_counts": counts,
        "has_settings": s.load_config() is not None,
        "update": {"available": bool(latest) and _version_tuple(latest) > _version_tuple(s.version),
                   "latest": latest},
    }


@router.get("/settings")
def get_settings(request: Request):
    return _services(request).load_config() or EMPTY_SETTINGS


@router.get("/app-settings")
def get_app_settings(request: Request):
    return _services(request).load_app_settings()


@router.get("/countries")
def get_countries(request: Request, lang: str = Query("ar")):
    if lang not in LANGUAGES:
        raise HTTPException(status_code=422, detail={"lang": "invalid"})
    return localized_countries(lang, _services(request).base_countries())


@router.get("/jobs")
def get_jobs(request: Request, status: str | None = None, source: str | None = None, text: str | None = None,
             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    include_dismissed = status == "all"
    if status == "all":
        status = None
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail={"status": "invalid"})
    with _services(request).store_factory() as store:
        jobs = store.list_jobs(status=status, source=source, text=text, include_dismissed=include_dismissed,
                               limit=limit, offset=offset)
        total = store.count_jobs(status=status, source=source, text=text, include_dismissed=include_dismissed)
    return {"jobs": jobs, "total": total}


@router.get("/runs")
def get_runs(request: Request, limit: int = Query(20, ge=1, le=100)):
    with _services(request).store_factory() as store:
        return {"runs": store.list_runs(limit=limit)}


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: int):
    with _services(request).store_factory() as store:
        details = store.run_details(run_id)
    if details is None:
        raise HTTPException(status_code=404, detail={"run": "not_found"})
    return details
```

- [ ] **Step 6: Create the placeholder UI `src/web/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hawkeye Jobs</title>
</head>
<body>
  <!-- Placeholder: replaced by the real UI in Plan 2b (docs/superpowers/plans). -->
  <h1>Hawkeye Jobs</h1>
  <p>The app is running. The full interface arrives with the front-end plan.</p>
</body>
</html>
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass, with no warnings in the output. httpx2 is installed, so FastAPI's TestClient raises no deprecation warning.

- [ ] **Step 8: Commit**

```bash
git add src/web tests/test_api.py
git commit -m "Add the local web server with token/Host guards and read-only API"
```

---

### Task 10: Write endpoints: settings, jobs, runs, and the SSE event stream

**Files:**
- Modify: `src/web/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `settings.validate_search_settings`, `SettingsError`, `USER_APP_KEYS` (Task 1); `RunInProgress` and `NoSearchSettings` (Task 6).
- Produces the following endpoints:

| Endpoint | Success | Errors |
|---|---|---|
| `PUT /api/settings` | 200 | 422 `{"errors": {...}}` |
| `PATCH /api/app-settings` | 200 | 422 `{"errors": {...}}`; only `USER_APP_KEYS` are accepted |
| `PATCH /api/jobs/{id}` | 200 `{"ok": true}` | 404 / 422 |
| `POST /api/runs` | 202 `{"run_id"}` | 409 `{"error": "run_in_progress"}`, 422 `{"error": "no_settings"}` |
| `POST /api/runs/current/cancel` | 202 | 409 `{"error": "not_running"}` |
| `GET /api/events` | SSE | — |

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py` (above the `__main__` guard):

```python
class WriteTests(ApiTestCase):
    def send(self, method, path, json=None):
        return self.client.request(method, path, headers={"X-Hawkeye-Token": TOKEN}, json=json)

    def test_put_settings_validates_and_saves(self):
        ok = self.send("PUT", "/api/settings", {"keywords": [" Java "], "locations": ["jo", "remote"],
                                                "sources": ["bayt"]})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.config["keywords"], ["Java"])
        bad = self.send("PUT", "/api/settings", {"keywords": [], "locations": ["xx"], "sources": []})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["errors"], {"keywords": "required", "locations": "invalid",
                                                "sources": "required"})

    def test_patch_app_settings_only_user_keys(self):
        ok = self.send("PATCH", "/api/app-settings", {"language": "en"})
        self.assertEqual((ok.status_code, ok.json()["language"]), (200, "en"))
        internal = self.send("PATCH", "/api/app-settings", {"last_posts_run": "2026-01-01T00:00:00Z"})
        self.assertEqual(internal.status_code, 422)
        self.assertEqual(internal.json()["errors"], {"last_posts_run": "unknown"})

    def test_patch_job_status(self):
        from store import job_id
        jid = job_id("https://a.com/1")
        self.assertEqual(self.send("PATCH", f"/api/jobs/{jid}", {"status": "saved"}).status_code, 200)
        self.assertEqual(self.get("/api/jobs?status=saved").json()["total"], 1)
        self.assertEqual(self.send("PATCH", "/api/jobs/nope", {"status": "saved"}).status_code, 404)
        self.assertEqual(self.send("PATCH", f"/api/jobs/{jid}", {"status": "archived"}).status_code, 422)

    def test_start_run(self):
        response = self.send("POST", "/api/runs")
        self.assertEqual((response.status_code, response.json()), (202, {"run_id": 42}))
        self.assertEqual(self.manager.started, ["manual"])

    def test_start_run_conflicts(self):
        self.manager.is_running = True
        self.assertEqual(self.send("POST", "/api/runs").json(), {"error": "run_in_progress"})
        self.assertEqual(self.send("POST", "/api/runs").status_code, 409)
        self.manager.is_running = False
        self.manager.no_settings = True
        response = self.send("POST", "/api/runs")
        self.assertEqual((response.status_code, response.json()), (422, {"error": "no_settings"}))

    def test_cancel(self):
        self.assertEqual(self.send("POST", "/api/runs/current/cancel").status_code, 409)
        self.manager.is_running = True
        self.assertEqual(self.send("POST", "/api/runs/current/cancel").status_code, 202)

    def test_events_stream(self):
        import threading

        def feed():
            import time
            while self.manager.queue is None:
                time.sleep(0.01)
            self.manager.queue.put({"type": "run_started", "run_id": 1, "total": 2})
            self.manager.queue.put(None)  # app shutting down: stream ends

        threading.Thread(target=feed, daemon=True).start()
        with self.client.stream("GET", f"/api/events?token={TOKEN}") as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            body = "".join(response.iter_text())
        self.assertIn('data: {"type": "run_started", "run_id": 1, "total": 2}', body)
        self.assertFalse(self.manager.subscribed)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_api -v`
Expected: FAIL / 405 for the new routes.

- [ ] **Step 3: Add the write endpoints and SSE to `src/web/api.py`**

Add `import json` and `import queue` to the imports. Add `from fastapi.responses import JSONResponse, StreamingResponse`, `from runner import NoSearchSettings, RunInProgress` and `from settings import SettingsError, USER_APP_KEYS, validate_search_settings`. Then append:

```python
KEEPALIVE_SECONDS = 15


@router.put("/settings")
def put_settings(request: Request, body: dict):
    s = _services(request)
    allowed = {c["id"] for c in s.base_countries()}
    try:
        cleaned = validate_search_settings(body, allowed)
    except SettingsError as exc:
        return JSONResponse({"errors": exc.errors}, status_code=422)
    s.save_config(cleaned)
    return cleaned


@router.patch("/app-settings")
def patch_app_settings(request: Request, body: dict):
    unknown = {key: "unknown" for key in body if key not in USER_APP_KEYS}
    if unknown:
        return JSONResponse({"errors": unknown}, status_code=422)
    try:
        return _services(request).save_app_settings(body)
    except SettingsError as exc:
        return JSONResponse({"errors": exc.errors}, status_code=422)


@router.patch("/jobs/{job_id}")
def patch_job(request: Request, job_id: str, body: dict):
    status = body.get("status")
    if status not in STATUSES:
        return JSONResponse({"errors": {"status": "invalid"}}, status_code=422)
    with _services(request).store_factory() as store:
        found = store.set_status(job_id, status)
    if not found:
        raise HTTPException(status_code=404, detail={"job": "not_found"})
    return {"ok": True}


@router.post("/runs", status_code=202)
def post_run(request: Request):
    try:
        run_id = _services(request).manager.start("manual")
    except RunInProgress:
        return JSONResponse({"error": "run_in_progress"}, status_code=409)
    except NoSearchSettings:
        return JSONResponse({"error": "no_settings"}, status_code=422)
    return {"run_id": run_id}


@router.post("/runs/current/cancel", status_code=202)
def cancel_run(request: Request):
    if not _services(request).manager.cancel():
        return JSONResponse({"error": "not_running"}, status_code=409)
    return {"ok": True}


def _event_stream(manager, q):
    try:
        yield "retry: 3000\n\n"
        while manager.is_subscribed(q):
            try:
                event = q.get(timeout=KEEPALIVE_SECONDS)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        manager.unsubscribe(q)


@router.get("/events")
def get_events(request: Request):
    manager = _services(request).manager
    q = manager.subscribe()
    return StreamingResponse(_event_stream(manager, q), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass, with pristine output.

- [ ] **Step 5: Commit**

```bash
git add src/web/api.py tests/test_api.py
git commit -m "API: save settings, job status, start/cancel runs, live event stream"
```

---

### Task 11: Action endpoints: open URL, data folder, LinkedIn login, report a problem, update check, reset

**Files:**
- Modify: `src/web/api.py`
- Create: `src/web/redact.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces:
  - `web.redact.redact(text: str, *, home: str, user: str) -> str`
  - `POST /api/open-url {url}` → 204. Only `http(s)` is allowed; anything else gets 422.
  - `POST /api/open-data-folder` → 204.
  - `POST /api/linkedin/login` → 202. Returns 409 `{"error": "run_in_progress" | "chrome_not_found"}`.
  - `POST /api/report-problem` → 200 `{"url"}`, and the URL is opened.
  - `GET /api/update` → `{"current", "latest", "available", "checked"}`. It uses the cache when `last_update_check` is under 24 h old, and returns `{"available": false, "disabled": true}` when `update_check` is off.
  - `POST /api/reset {confirm: "DELETE"}` → 204. Returns 422 without the confirmation, and 409 while running.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
from web.redact import redact


class RedactTests(unittest.TestCase):
    def test_redact_removes_personal_data(self):
        text = ("Copied /Users/sam/Library/Application Support/hawkeye-jobs/config.json for sam\n"
                "login sam.smith@gmail.com failed token=abc123 key=zzz password=hunter2 GMAIL_PASS=xyzw\n"
                "GET https://www.linkedin.com/search/results/content/?keywords=java&session=s3cr3t")
        out = redact(text, home="/Users/sam", user="sam")
        for secret in ("/Users/sam", "sam.smith@gmail.com", "abc123", "zzz", "hunter2", "xyzw", "s3cr3t",
                       "keywords=java"):
            self.assertNotIn(secret, out)
        self.assertIn("~/Library/Application Support/hawkeye-jobs/config.json", out)
        self.assertIn("<user>", out)
        self.assertIn("https://www.linkedin.com", out)


class ActionTests(ApiTestCase):
    def send(self, method, path, json=None):
        return self.client.request(method, path, headers={"X-Hawkeye-Token": TOKEN}, json=json)

    def test_open_url_only_http(self):
        self.assertEqual(self.send("POST", "/api/open-url", {"url": "https://a.com/1"}).status_code, 204)
        self.assertEqual(self.opened_urls, ["https://a.com/1"])
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "", None):
            self.assertEqual(self.send("POST", "/api/open-url", {"url": bad}).status_code, 422)

    def test_open_data_folder(self):
        self.assertEqual(self.send("POST", "/api/open-data-folder").status_code, 204)
        self.assertEqual(len(self.opened_paths), 1)

    def test_linkedin_login(self):
        self.assertEqual(self.send("POST", "/api/linkedin/login").status_code, 202)
        argv = self.launched[0]
        self.assertTrue(argv[0].endswith("Google Chrome"))
        self.assertTrue(any(a.startswith("--user-data-dir=") for a in argv))
        self.assertEqual(argv[-1], "https://www.linkedin.com/login")

    def test_linkedin_login_refused_while_running_or_without_chrome(self):
        self.manager.is_running = True
        self.assertEqual(self.send("POST", "/api/linkedin/login").json(), {"error": "run_in_progress"})
        self.manager.is_running = False
        self.services.find_chrome = lambda: None
        response = self.send("POST", "/api/linkedin/login")
        self.assertEqual((response.status_code, response.json()), (409, {"error": "chrome_not_found"}))

    def test_report_problem_builds_redacted_issue_url(self):
        (Path(self.tmp.name) / "hawkeye.log").write_text(
            f"line one {self.services.home}/x for sam@example.com\n", encoding="utf-8")
        response = self.send("POST", "/api/report-problem")
        url = response.json()["url"]
        self.assertTrue(url.startswith("https://github.com/Abdallahabusnineh/hawkeye-jobs/issues/new?"))
        self.assertEqual(self.opened_urls, [url])
        from urllib.parse import parse_qs, urlparse
        body = parse_qs(urlparse(url).query)["body"][0]
        self.assertIn("1.0.0", body)
        self.assertIn("macOS 15", body)
        self.assertNotIn(self.services.home, body)
        self.assertNotIn("sam@example.com", body)

    def test_update_check_and_cache(self):
        self.release = {"tag_name": "v1.2.0"}
        first = self.get("/api/update").json()
        self.assertEqual((first["available"], first["latest"]), (True, "1.2.0"))
        self.release = {"tag_name": "v9.9.9"}  # cached for 24 h: not fetched again
        self.assertEqual(self.get("/api/update").json()["latest"], "1.2.0")

    def test_update_check_disabled_or_failing(self):
        self.app_settings["update_check"] = False
        self.assertEqual(self.get("/api/update").json(), {"available": False, "disabled": True})
        self.app_settings["update_check"] = True

        def boom():
            raise OSError("offline")
        self.services.fetch_latest_release = boom
        with self.assertLogs("web.api", "WARNING"):
            body = self.get("/api/update").json()
        self.assertEqual((body["available"], body["latest"]), (False, None))

    def test_reset_requires_confirmation_and_idle(self):
        self.assertEqual(self.send("POST", "/api/reset", {}).status_code, 422)
        self.manager.is_running = True
        self.assertEqual(self.send("POST", "/api/reset", {"confirm": "DELETE"}).status_code, 409)
        self.manager.is_running = False
        self.assertEqual(self.send("POST", "/api/reset", {"confirm": "DELETE"}).status_code, 204)
        self.assertFalse(self.db.exists())
        self.assertEqual(self.get("/api/state").json()["job_counts"]["new"], 0)  # fresh DB on next open
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_api -v`
Expected: ImportError for `web.redact`.

- [ ] **Step 3: Create `src/web/redact.py`**

```python
"""
redact.py — Strip personal data before the user shares a log excerpt
=====================================================================
"Report a problem" opens a prefilled GitHub issue in the user's browser. The
user sees and submits it themselves; this makes sure the text never contains
their home path, user name, e-mail addresses, secrets or full search URLs.
"""

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SECRET = re.compile(r"(?i)\b(token|key|password|passwd|secret|gmail_pass)\s*[=:]\s*\S+")
_URL = re.compile(r"(https?://[^/\s?#]+)[^\s]*")


def redact(text: str, *, home: str, user: str) -> str:
    if home:
        text = text.replace(home, "~")
    text = _EMAIL.sub("<email>", text)
    text = _SECRET.sub(lambda m: f"{m.group(1)}=<hidden>", text)
    text = _URL.sub(lambda m: m.group(1), text)
    if user:
        text = re.sub(rf"(?<![\w.-]){re.escape(user)}(?![\w.-])", "<user>", text)
    return text
```

- [ ] **Step 4: Add the action endpoints to `src/web/api.py`**

Add `from datetime import datetime, timezone`, `from urllib.parse import urlencode, urlparse` and `from web.redact import redact` to the imports, and append:

```python
ISSUE_URL = "https://github.com/Abdallahabusnineh/hawkeye-jobs/issues/new"
LOG_LINES = 200
UPDATE_CACHE_SECONDS = 24 * 3600


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


@router.post("/open-url", status_code=204)
def open_url(request: Request, body: dict):
    url = body.get("url")
    if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
        return JSONResponse({"errors": {"url": "invalid"}}, status_code=422)
    _services(request).open_url(url)


@router.post("/open-data-folder", status_code=204)
def open_data_folder(request: Request):
    s = _services(request)
    s.open_path(s.data_dir())


@router.post("/linkedin/login", status_code=202)
def linkedin_login(request: Request):
    s = _services(request)
    if s.manager.is_running:
        return JSONResponse({"error": "run_in_progress"}, status_code=409)
    chrome = s.find_chrome()
    if not chrome:
        return JSONResponse({"error": "chrome_not_found"}, status_code=409)
    s.launch([chrome, f"--user-data-dir={s.linkedin_profile_dir()}", "https://www.linkedin.com/login"])
    return {"ok": True}


@router.post("/report-problem")
def report_problem(request: Request):
    s = _services(request)
    try:
        lines = s.log_path().read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_LINES:]
    except OSError:
        lines = []
    excerpt = redact("\n".join(lines), home=s.home, user=s.user)
    body = (f"**Version:** {s.version}\n**System:** {s.os_name}\n\n"
            "**What happened?**\n\n\n"
            f"<details><summary>Log (last {LOG_LINES} lines, personal data removed)</summary>\n\n"
            f"```\n{excerpt}\n```\n</details>\n")
    url = f"{ISSUE_URL}?{urlencode({'title': 'Problem report', 'body': body})}"
    s.open_url(url)
    return {"url": url}


@router.get("/update")
def check_update(request: Request):
    s = _services(request)
    app = s.load_app_settings()
    if not app["update_check"]:
        return {"available": False, "disabled": True}
    checked = _parse_iso(app.get("last_update_check"))
    latest = app.get("latest_version")
    if checked is None or s.clock() - checked >= UPDATE_CACHE_SECONDS:
        try:
            latest = str(s.fetch_latest_release().get("tag_name", "")).lstrip("vV") or None
        except Exception:
            log.warning("Update check failed", exc_info=True)
            latest = None
        s.save_app_settings({"last_update_check": _iso(s.clock()), "latest_version": latest})
        checked = s.clock()
    return {"current": s.version, "latest": latest, "checked": _iso(checked),
            "available": bool(latest) and _version_tuple(latest) > _version_tuple(s.version)}


@router.post("/reset", status_code=204)
def reset(request: Request, body: dict):
    s = _services(request)
    if body.get("confirm") != "DELETE":
        return JSONResponse({"errors": {"confirm": "required"}}, status_code=422)
    if s.manager.is_running:
        return JSONResponse({"error": "run_in_progress"}, status_code=409)
    probe = s.store_factory()
    db_path = probe.path
    probe.close()
    for suffix in ("", "-wal", "-shm"):
        target = db_path.with_name(db_path.name + suffix)
        if target.exists():
            target.unlink()
```

The `test_update_check_and_cache` test relies on `save_app_settings` accepting `last_update_check` and `latest_version`. The test's fake only rejects keys that are missing from its dict, and both keys are present, so it passes. The real `save_app_settings` accepts them too (Task 1, internal keys). Only `PATCH /api/app-settings` restricts input to user keys.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/web/api.py src/web/redact.py tests/test_api.py
git commit -m "API actions: open links safely, LinkedIn login, redacted problem report, update check, reset"
```

---

### Task 12: App launcher (`app.py`), shutdown, docs

**Files:**
- Create: `src/app.py`, `tests/test_app.py`
- Modify: `README.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes everything above.
- Produces:
  - `app.free_port() -> int`
  - `app.window_url(port, token) -> str`
  - `app.build(port=None) -> AppParts`: a dataclass with `token, port, manager, auto, server, fastapi_app`
  - `app.shutdown(parts, wait_seconds=35)`
  - `app.main(argv=None)`: `--no-window` serves and prints the URL, for front-end development and browser checks

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app.py`:

```python
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import app


class AppTests(unittest.TestCase):
    def test_free_port_is_bindable_on_localhost(self):
        port = app.free_port()
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))

    def test_window_url_carries_token(self):
        self.assertEqual(app.window_url(5000, "abc"), "http://127.0.0.1:5000/?token=abc")

    def test_build_wires_guarded_app(self):
        parts = app.build(port=5555)
        self.assertGreaterEqual(len(parts.token), 32)
        self.assertEqual(parts.server.config.host, "127.0.0.1")
        self.assertEqual(parts.server.config.port, 5555)
        from fastapi.testclient import TestClient
        client = TestClient(parts.fastapi_app, base_url="http://127.0.0.1:5555")
        self.assertEqual(client.get("/api/state").status_code, 403)
        self.assertEqual(client.get("/api/state", headers={"X-Hawkeye-Token": parts.token}).status_code, 200)

    def test_shutdown_cancels_waits_and_stops_everything(self):
        parts = MagicMock()
        app.shutdown(parts, wait_seconds=1)
        parts.manager.cancel.assert_called_once()
        parts.manager.wait.assert_called_once_with(1)
        parts.auto.stop.assert_called_once()
        parts.manager.close_subscribers.assert_called_once()
        self.assertTrue(parts.server.should_exit)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_app -v`
Expected: ERROR `No module named 'app'`.

- [ ] **Step 3: Create `src/app.py`**

```python
#!/usr/bin/env python3
"""
app.py — Hawkeye Jobs desktop app
==================================
Starts the local web server (127.0.0.1, random port, per-launch token) on a
background thread and opens a native window (pywebview) on it. Everything
stays on the user's machine. Closing the window stops any running search
gracefully (in-flight requests finish, Chrome closes), then exits.

    python src/app.py               # open the app window
    python src/app.py --no-window   # serve only and print the URL (UI development)
"""

import argparse
import logging
import os
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import uvicorn

from logs import setup_logging
from notify import DesktopNotifier
from runner import AutoSearch, RunManager
from settings import load_app_settings, migrate_legacy_config
from store import Store
from web.server import create_app
from web.services import AppServices

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
WINDOW_TITLE = "Hawkeye Jobs"
SHUTDOWN_WAIT_SECONDS = 35


@dataclass
class AppParts:
    token: str
    port: int
    manager: RunManager
    auto: AutoSearch
    server: uvicorn.Server
    fastapi_app: object


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def window_url(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/?token={token}"


def _prepare_data() -> None:
    migrate_legacy_config(ROOT)
    with Store() as store:
        store.recover_interrupted_runs()
        store.import_legacy_seen_file(ROOT / "seen_jobs.json")


def build(port=None) -> AppParts:
    port = port or free_port()
    token = secrets.token_urlsafe(32)
    manager = RunManager(notifier=DesktopNotifier(lambda: load_app_settings()["language"]))
    auto = AutoSearch(manager)
    fastapi_app = create_app(token=token, allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
                             services=AppServices.default(manager))
    server = uvicorn.Server(uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, lifespan="off"))
    return AppParts(token=token, port=port, manager=manager, auto=auto, server=server, fastapi_app=fastapi_app)


def _serve(parts: AppParts) -> threading.Thread:
    thread = threading.Thread(target=parts.server.run, name="hawkeye-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not parts.server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the local server did not start")
        time.sleep(0.05)
    return thread


def shutdown(parts, wait_seconds: float = SHUTDOWN_WAIT_SECONDS) -> None:
    """Stop a running search gracefully, then the scheduler, event streams and server."""
    parts.manager.cancel()
    parts.manager.wait(wait_seconds)
    parts.auto.stop()
    parts.manager.close_subscribers()
    parts.server.should_exit = True


def main(argv=None) -> None:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[1]).parse_known_args(argv)[0] \
        if False else _parse(argv)
    setup_logging()
    _prepare_data()
    parts = build()
    thread = _serve(parts)
    parts.auto.start()
    parts.manager.report_pending_now()
    url = window_url(parts.port, parts.token)
    try:
        if args.no_window:
            print(f"Hawkeye Jobs is running at {url}  (Ctrl+C to stop)")
            while thread.is_alive():
                time.sleep(0.5)
        else:
            import webview
            webview.create_window(WINDOW_TITLE, url, width=1200, height=800, min_size=(360, 600))
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(parts)
        thread.join(timeout=5)


def _parse(argv):
    parser = argparse.ArgumentParser(prog="app.py", description="Hawkeye Jobs desktop app")
    parser.add_argument("--no-window", action="store_true", help="serve only and print the URL")
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
```

Simplify `main`'s first line to `args = _parse(argv)`. The conditional expression above is a leftover; do not keep it. Final form:

```python
def main(argv=None) -> None:
    args = _parse(argv)
    setup_logging()
    ...
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest discover -s tests -v`
Expected: all pass, with no window opened and no network used. `build()` constructs the objects but does not start the server.

- [ ] **Step 5: Manual smoke check (local only, scratch data)**

```bash
HAWKEYE_DATA_DIR=$(mktemp -d) venv/bin/python3 src/app.py --no-window
```

Expected: it prints `Hawkeye Jobs is running at http://127.0.0.1:<port>/?token=…`. Then, from another shell:
- `curl -s http://127.0.0.1:<port>/` returns the placeholder HTML;
- `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<port>/api/state` returns `403`;
- with `-H "X-Hawkeye-Token: <token>"` it returns `200`.

Stop with Ctrl+C. It must exit within a few seconds without a traceback. Do not start a search.

- [ ] **Step 6: Docs**

**`README.md`:** add a "Desktop app (preview)" section:

```markdown
## Desktop app (preview)

    venv/bin/python3 src/app.py

Opens the Hawkeye Jobs window. Everything runs on your computer: the window talks to a small server on 127.0.0.1 that only this app can use. The interface is being built; downloadable installers come later.
```

**`CLAUDE.md`:**
- Under "Running the scraper", add `venv/bin/python3 src/app.py   # desktop app (--no-window to serve only)`.
- Add the following rows to the Key files table:

| File | What it does |
|---|---|
| `src/app.py` | Desktop launcher |
| `src/runner.py` | One search at a time, live events, auto-search |
| `src/reporting.py` | Report new jobs, then mark them |
| `src/web/` | Local API and UI |

**`docs/ARCHITECTURE.md`:** add a "Desktop app" section summarising spec §3–§7:
- app.py, the server guards, the RunManager/AutoSearch flow, the run lock, reporting and notifications;
- schema v3 `run_queries` and `trigger`;
- the endpoint table, copied from spec §4 with the final status codes from Tasks 10–11.

- [ ] **Step 7: Full suite and commit**

Run: `venv/bin/python3 -m unittest discover -s tests -v`. Expected: all pass, with pristine output.

```bash
git add src/app.py tests/test_app.py README.md CLAUDE.md docs/ARCHITECTURE.md
git commit -m "Add the desktop app launcher with graceful shutdown; document the app backend"
```
