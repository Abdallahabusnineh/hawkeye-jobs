# Core pipeline refactor — design spec

**Status:** draft for review · **Date:** 2026-10-10 · **Sub-project:** 1 of 4 (Phase 1)

---

## 1. Product context

hawkeye-jobs is a free, non-profit, open-source tool that helps people find jobs. Today it is a terminal program, which most job seekers cannot use. The goal is a desktop app that anyone on macOS, Windows, or Linux can download and use without a terminal.

### What matters to a job seeker

1. **Reach a job early.** People who apply in the first day or two have a much better chance.
2. **See only relevant jobs.** Every irrelevant or duplicate listing costs attention.
3. **The tool must not cost time.** No hard install, no long waits, no need to remember to run it.
4. **Trust.** Easy install, data stays on the user's machine, accounts are never put at risk.

### Decisions already made

- **Desktop app, no server.** Each user scrapes from their own machine (their IP, their LinkedIn login, zero hosting cost). A server would be blocked quickly (one shared IP), would have to hold users' LinkedIn sessions, and would cost money per user. The website is a download page only.
- **Stack:** Python core (this repo) + FastAPI + a light HTML/JS frontend inside a pywebview window, packaged with PyInstaller per OS via GitHub Actions.
- **Terminal mode stays** for developers and existing users.

### Roadmap

**Phase 1 — v1.0**, split into four sub-projects, each with its own spec → plan → implementation:

1. **Core** (this spec): shared pipeline, progress events, cancellation, SQLite storage, per-OS data directory, speed improvements.
2. **App:** FastAPI + UI + pywebview window. Arabic + English (RTL) from day one. Job status tracking (new / saved / applied / dismissed). Auto-search every few hours while the app is open, plus a desktop notification for new jobs. Chrome-missing guidance. In-app LinkedIn login button. LinkedIn posts are off by default, show an account-risk warning when enabled, and run at most once a day. Update-available notice. "Report a problem" button that opens a prefilled GitHub issue with a log excerpt (nothing is sent automatically).
3. **Packaging and distribution:** PyInstaller builds for macOS / Windows / Linux on GitHub Actions, GitHub Releases, code signing (SignPath Foundation for Windows; Apple nonprofit fee waiver if eligible), and a daily CI canary that checks every source still parses.
4. **Download page:** GitHub Pages.

**Phase 2 — quality:** merge the same job across boards (title + company, not just URL), rank results by match, keyword suggestions (e.g. Arabic role → English titles), remote selector updates (data only, no code), start at login / tray.

**Phase 3 — reach:** regional sources (Akhtaboot, Wuzzuf, GulfTalent, Tanqeeb), Telegram / email notifications.

**Deliberately not planned:** a server, user accounts, telemetry, AI-written cover letters.

---

## 2. Scope of this spec

### Goals

- **G1** One core used by both the terminal and the app: `pipeline.run_search()`.
- **G2** Structured progress events (data, never English sentences) and cancellation.
- **G3** Store full jobs and their status in SQLite, replacing `seen_jobs.json`.
- **G4** Per-OS data directory, plus migration of existing users' `config.json` and `seen_jobs.json`.
- **G5** Faster runs: HTTP and Chrome sources run in parallel; fixed sleeps are replaced by waiting for job cards.
- **G6** Logging to a rotating file, the only debugging channel a packaged app has.
- **G7** Terminal mode keeps today's behaviour: same prompts, prints new jobs, emails new jobs.

### Non-goals (handled by later sub-projects)

UI, HTTP API, scheduling, notifications, packaging, translated strings, LinkedIn-posts frequency limit, cross-board fuzzy dedup, remote selectors, deleting old jobs.

---

## 3. Module layout

| File | Change |
|---|---|
| `src/paths.py` | **New.** Resolves the data directory and every file path inside it. |
| `src/logs.py` | **New.** `setup_logging()`: rotating file handler. |
| `src/store.py` | **New.** SQLite store. Replaces `tracker.py`. Owns `job_id()` and legacy `seen_jobs.json` import. |
| `src/pipeline.py` | **New.** `run_search()`: plans queries, runs the two lanes, saves to the store, emits events. |
| `src/catalog.py` | Adds `plan_queries()`. `expand_searches()` is rebuilt on top of it with identical output. |
| `src/browser.py` | Adds `find_chrome()` (incl. Windows paths), `ChromeNotFound`, `wait_for()`. Windows version detection. |
| `src/scraper.py`, `bayt.py`, `naukrigulf.py`, `google_jobs.py`, `linkedin_posts.py` | Loop body becomes a single-query `fetch_*()` that **raises** on error. Old list functions removed. `print` → `logging`. Fixed sleeps → `wait_for()`. |
| `src/linkedin_posts.py` | Also raises `LinkedInLoginRequired` instead of returning `[]`. |
| `src/settings.py` | Config path from `paths.py`. `save_config()` merges instead of overwriting. Legacy config copy. |
| `src/countries.py` | Cache path from `paths.py`. |
| `src/main.py` | Becomes the terminal front-end only (see §9). |
| `src/tracker.py` | **Removed.** |
| `src/filters.py`, `utils.py`, `picker.py`, `email_sender.py` | Unchanged. |
| `requirements.txt` | Adds `platformdirs`. (`selenium` already comes with `undetected-chromedriver`.) |

---

## 4. Data directory (`paths.py`)

```python
def data_dir() -> Path            # created on first call
def db_path() -> Path             # <data>/hawkeye.db
def config_path() -> Path         # <data>/config.json
def countries_cache_path() -> Path
def log_path() -> Path            # <data>/hawkeye.log
def linkedin_profile_dir() -> Path
```

`data_dir()` resolution order:

1. `HAWKEYE_DATA_DIR` env var, if set.
2. `platformdirs.user_data_dir("hawkeye-jobs", appauthor=False)`:
   - macOS: `~/Library/Application Support/hawkeye-jobs`
   - Windows: `C:\Users\<user>\AppData\Local\hawkeye-jobs`
   - Linux: `~/.local/share/hawkeye-jobs` (honours `XDG_DATA_HOME`)

`linkedin_profile_dir()` resolution order:

1. `LINKEDIN_PROFILE_DIR` env var.
2. `~/.hawkeye-linkedin-profile`, if it exists. Existing users keep their login.
3. `<data>/linkedin-profile/`.

All paths are resolved at call time, not at import time, so tests can set `HAWKEYE_DATA_DIR`.

### Legacy migration

This only matters for people running from the repo. A packaged app has no legacy files.

- **Config** (`settings.migrate_legacy_config(root=ROOT)`): if `config_path()` does not exist and `<repo>/config.json` does, **copy** it. The original is never deleted.
- **Seen jobs** (`Store.import_legacy_seen_file(path)`): read the old `{"seen": [...]}` format and `INSERT OR IGNORE` into `legacy_seen`. It is idempotent, so no "already imported" flag is needed.
- `countries_cache.json` is not migrated. It is re-fetched.
- One log line (and one terminal line in terminal mode) says where the data now lives.

**Reset** changes from `rm seen_jobs.json` to deleting `hawkeye.db`. README / ARCHITECTURE / CLAUDE.md are updated accordingly.

---

## 5. Data model (`store.py`)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,                 -- ISO 8601 UTC
  finished_at   TEXT,
  state         TEXT NOT NULL CHECK (state IN ('running','done','cancelled','failed')),
  settings_json TEXT NOT NULL,
  found_count   INTEGER NOT NULL DEFAULT 0,
  new_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE jobs (
  id                TEXT PRIMARY KEY,          -- job_id(): MD5 of cleaned URL (same as tracker.py)
  title             TEXT NOT NULL,
  url               TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  source            TEXT NOT NULL,
  location          TEXT NOT NULL DEFAULT '',  -- site-specific string, as scraped today
  location_id       TEXT,                      -- country id ('jo', 'remote'); NULL for LinkedIn posts
  keyword           TEXT NOT NULL DEFAULT '',  -- the user keyword that first found it
  published         TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','saved','applied','dismissed')),
  status_changed_at TEXT,
  first_seen_at     TEXT NOT NULL,
  last_seen_at      TEXT NOT NULL,
  first_run_id      INTEGER REFERENCES runs(id)
);
CREATE INDEX jobs_by_status_seen ON jobs (status, first_seen_at DESC);

CREATE TABLE legacy_seen (id TEXT PRIMARY KEY);

PRAGMA user_version = 1;
```

`location_id` and `keyword` let the UI filter by country, and show Arabic country names, without parsing site-specific strings.

### API

```python
def job_id(url: str) -> str          # MD5(clean_url(url).strip().rstrip("/")), identical to tracker._job_id

class Store:
    def __init__(self, path: Path | None = None)      # default paths.db_path(); creates schema
    def recover_interrupted_runs(self) -> int         # 'running' → 'failed'
    def import_legacy_seen_file(self, path: Path) -> int
    def start_run(self, settings: dict) -> int
    def add_jobs(self, run_id: int, jobs: list[dict]) -> list[dict]   # returns only new jobs (with "id")
    def finish_run(self, run_id: int, state: str, found: int, new: int) -> None
    def set_status(self, job_id: str, status: str) -> bool            # ValueError on bad status
    def list_jobs(self, status=None, source=None, run_id=None, text=None,
                  include_dismissed=False, limit=100, offset=0) -> list[dict]
    def list_runs(self, limit=20) -> list[dict]
```

### Semantics

**`add_jobs`**, for each job:
- If its id is in `legacy_seen`, skip it.
- Else, if the id is already in `jobs`, update `last_seen_at`. The status is unchanged and the job is not counted as new.
- Else, insert it with `status='new'` and `first_run_id=run_id`, and count it as new.
- A duplicate inside the same batch is not new the second time.

**Status** changes only through `set_status`, meaning only by the user.

**`list_jobs`:**
- With `status=None`, it returns everything except `dismissed`, unless `include_dismissed=True`.
- `text` matches title/description with `LIKE`.
- Ordered by `first_seen_at DESC`.

### Threading

- **One `Store` per thread.** `sqlite3`'s `check_same_thread` stays on. Opening a store is cheap.
- **WAL mode and `busy_timeout = 5000`.** Readers never block the writer.

**`recover_interrupted_runs()` must be called once at process start, never while a search is running.** Otherwise it would mark the live run as failed.

---

## 6. Query planning (`catalog.py`)

```python
def plan_queries(keywords, location_ids, source_ids, country_labels=None) -> list[dict]
# [{"source": "bayt", "keyword": "Java", "location_id": "jo", "args": ("java", "jordan")}, ...]
```

- `args` is exactly the item `expand_searches()` puts in its per-source list today.
- `location_id` is normalised with `normalize_location_id()`. It is `None` for `linkedin_posts`, which is not location-scoped.
- Order is the same as today's `expand_searches()` iteration.
- `expand_searches()` becomes "group `plan_queries()` by source". All existing `test_catalog.py` tests must pass unchanged.

---

## 7. Scrapers

### Single-query functions

| Source | Function |
|---|---|
| `linkedin` | `scraper.fetch_linkedin(args, seen_urls, keywords)` |
| `indeed` | `scraper.fetch_indeed(args, seen_urls, keywords)` |
| `bayt` | `bayt.fetch_bayt(driver, args, seen_urls, keywords)` |
| `naukrigulf` | `naukrigulf.fetch_naukrigulf(driver, args, seen_urls, keywords)` |
| `google_jobs` | `google_jobs.fetch_google_jobs(driver, args, seen_urls, keywords)` |
| `linkedin_posts` | `linkedin_posts.fetch_linkedin_posts(driver, args, seen_urls, keywords)` |

- Each returns `list[dict]` in today's job-dict shape and **raises** on any error.
- `fetch_linkedin_posts` raises `LinkedInLoginRequired` when `_is_logged_out()` is true.
- Parsers (`_parse_*`) are unchanged apart from `print` → `logger.debug`.

### Smart waits

`browser.wait_for(driver, css, timeout) -> bool` uses `WebDriverWait` + `presence_of_element_located`.

| Source | Waits for | Timeout |
|---|---|---|
| Bayt | `li[data-job-id]` | 20 s (Cloudflare) |
| NaukriGulf | `.ng-box.srp-tuple` | 15 s |
| Google Jobs | `.PUpOsf` | 15 s |
| LinkedIn posts | `div[data-urn*="urn:li:activity"]` | 15 s; the scroll loop after it is unchanged |

- Timeouts are module constants.
- On timeout, the page is parsed as-is. If it yields 0 jobs, log a warning with the URL and `driver.title`. This is not an error.
- The 3 s settle sleep after starting Chrome is removed.

### `browser.py`

**`find_chrome() -> str | None` checks, in order:**
- macOS: the app bundle path.
- Windows: `%PROGRAMFILES%`, `%PROGRAMFILES(X86)%`, `%LOCALAPPDATA%` + `\Google\Chrome\Application\chrome.exe`.
- PATH: `google-chrome`, `google-chrome-stable`, `chrome`.

**Chrome major version:**
- On Windows, `chrome.exe --version` prints nothing, so read `HKCU\Software\Google\Chrome\BLBeacon\version` via `winreg`.
- Elsewhere, keep running `--version`.
- If detection fails, fall back to `None` (undetected-chromedriver auto-resolves).

**`create_driver()`:**
- Raises `ChromeNotFound` when `find_chrome()` is `None`.
- Otherwise passes `browser_executable_path=find_chrome()`.
- The module docstring is updated: there is no hardcoded `version_main`.

---

## 8. Pipeline (`pipeline.py`)

```python
@dataclass
class RunSummary:
    run_id: int
    state: str              # 'done' | 'cancelled' | 'failed'
    found: int
    new_jobs: list[dict]

def run_search(settings: dict,
               on_event: Callable[[dict], None] | None = None,
               cancel: threading.Event | None = None,
               *,
               country_labels: dict | None = None,
               store: Store | None = None,
               fetchers: dict | None = None,          # source → fetch fn; for tests
               driver_factory: Callable | None = None # for tests
               ) -> RunSummary
```

- `settings` holds `{keywords, locations, sources}`. Extra keys are ignored.
- `country_labels` comes from `load_countries()`, which the caller provides.
- The default `driver_factory` is `create_driver(headless=False, user_data_dir=linkedin_profile_dir())`.

### Flow

1. `queries = plan_queries(...)`. Each query gets `index` = its position (0 … total-1).
2. `run_id = store.start_run(settings)`, then emit `run_started`.
3. Split the queries into two lanes and start a thread for each non-empty lane:
   - **HTTP lane:** `linkedin`, `indeed`, sequential.
   - **Browser lane:** `bayt`, `naukrigulf`, `google_jobs`, `linkedin_posts`, sequential, in that order. `linkedin_posts` runs last, which reduces overlap with JobSpy's LinkedIn calls.
4. Each lane has its own `seen_urls` set. It checks `cancel` before every query and pushes messages onto a `queue.Queue`. **Lanes never touch the store and never call `on_event`.**
5. The **calling thread** drains the queue until both lanes finish. For each finished query it:
   - stamps `keyword` and `location_id` onto each job;
   - calls `store.add_jobs()`;
   - emits the event.

   Jobs are saved after every query, so partial results survive a crash.
6. At the end: `state = 'cancelled' if cancel.is_set() else 'done'`, then `finish_run`, then emit `run_finished`, then return.

`found` is the total number of jobs returned by the fetchers (after per-lane URL dedup). `new` is the number `add_jobs()` reported as new.

### Browser lane details

- **Start:**
  - `ChromeNotFound` → `browser_failed{reason:'chrome_not_found'}`.
  - Any other start error → `browser_failed{reason:'start_failed'}`.
  - In both cases every browser query → `query_skipped{reason:'browser_unavailable'}`. The HTTP lane is unaffected.
- **Crash detection:** after a query exception, probe the driver (`driver.window_handles`). If the probe raises, the driver is dead. Restart it **once**. On a second death, emit `browser_failed{reason:'crashed'}` and skip the remaining browser queries with `reason:'browser_unavailable'`.
- **`LinkedInLoginRequired`:** emit `login_required`, and skip the remaining `linkedin_posts` queries with `reason:'login_required'`.
- `driver.quit()` always runs, in `finally`, with errors swallowed.

### Events

Every event is a dict with a `type` key. Field values are data only.

| type | fields |
|---|---|
| `run_started` | `run_id`, `total` |
| `query_started` | `index`, `source`, `keyword`, `location_id` |
| `query_done` | `index`, `source`, `keyword`, `location_id`, `found`, `new` |
| `query_failed` | `index`, `source`, `keyword`, `location_id`, `error` (exception class name) |
| `query_skipped` | `index`, `source`, `keyword`, `location_id`, `reason` ∈ `login_required`, `browser_unavailable` |
| `login_required` | `source` |
| `browser_failed` | `reason` ∈ `chrome_not_found`, `start_failed`, `crashed` |
| `run_finished` | `run_id`, `state`, `found`, `new` |

**Guarantees:**
- `run_started` is always first and `run_finished` always last.
- A query's `query_started` comes before its `query_done` / `query_failed`. Skipped queries have no `query_started`.
- Events from the two lanes may interleave.
- `on_event` is only ever called on the thread that called `run_search`.
- If `on_event` raises, the exception is logged and ignored.
- On cancel, lanes stop without emitting skip events.

### Polite throttling

Each lane waits on a `Throttle` (`src/throttle.py`) before every query: a random minimum gap between the **start** of consecutive requests to the same host (page-load time counts toward the gap). This protects users' accounts and IPs from bot detection (LinkedIn account restrictions, Google CAPTCHAs, Bayt/Cloudflare challenges, HTTP 429). Slower is acceptable; harm is not. The first request never waits; the wait sleeps in slices of at most 0.5 s and stops as soon as `cancel` is set (the lane then ends as on its pre-query cancel check, with no events for that query).

- **Host groups:** throttles are keyed by `pipeline.SITE_GROUPS` (`linkedin` and `linkedin_posts` both map to `linkedin.com`), built once in `run_search` and shared by both lanes. Slots are reserved under a lock, so two lanes never get requests closer than the gap, including on the user's own LinkedIn account. A group uses the per-field largest (min, max) among its sources, so linkedin.com gets 10-20 s.
- **Back-off:** when a query fails (not on cancel), its group's gap doubles for the rest of the run, capped at 4x (`Throttle.back_off()`).
- **Across runs:** spacing persists. Before the lanes start, `run_search` reads `store.site_last_requests()` and `prime()`s each host group's throttle with its saved Unix time, so the first request of a new run waits out the remaining gap. After the lanes are joined it saves `throttle.last_request_wall()` per group with `store.save_site_requests()` (table `site_requests`; the time never moves backwards). The save is best effort (failures are logged, Ctrl+C is retried) and also happens when the run is cancelled or failed, since requests that were sent still count. Lanes never touch the store; priming and saving run on the calling thread. `delays={}` throttles nothing and saves nothing.

| Source | Gap (seconds, min-max) |
|---|---|
| linkedin | 3-6 (shares linkedin.com with linkedin_posts, so 10-20) |
| indeed | 3-6 |
| bayt | 6-10 |
| naukrigulf | 4-7 |
| google_jobs | 8-12 |
| linkedin_posts | 10-20 (the user's own LinkedIn account) |

Values live in `pipeline.POLITE_DELAYS`. `run_search(..., delays={...})` overrides them (tests pass `{}`); a source missing from `delays` is not throttled.

### Failure handling

| Situation | Behaviour |
|---|---|
| A query raises | `query_failed`; traceback to log; lane continues |
| LinkedIn / Indeed rate limit (429) | Same as any failure; **no retry** |
| Unexpected error in the coordinator (e.g. store write fails after `busy_timeout`) | Set `cancel`, join lanes, best-effort `finish_run(state='failed')`, emit `run_finished{state:'failed'}`, log traceback, return a summary. **Does not raise.** |
| App killed mid-run | Next start: `recover_interrupted_runs()` marks it `failed` |

**Cancel latency:** at most one in-flight query per lane, meaning one JobSpy call or one page load + wait (≤ ~35 s).

---

## 9. Terminal mode (`main.py`)

Startup:
1. `_load_dotenv()`
2. `setup_logging()`
3. `settings.migrate_legacy_config()`
4. `store = Store()`
5. `store.recover_interrupted_runs()`
6. `store.import_legacy_seen_file(ROOT / "seen_jobs.json")`, if it exists

Then:
- `collect_settings()` prompts exactly as today.
- `load_countries()` provides the labels.
- `run_search(..., on_event=print_event)`. `print_event` turns events into English terminal lines similar to today's output.
- **Ctrl+C** sets `cancel`. The current queries finish, results are saved, Chrome closes, and the run ends as `cancelled`. The coordinator catches `KeyboardInterrupt` for this.
- If `summary.new_jobs` is non-empty, print them and call `send_email()` as today.

---

## 10. Logging (`logs.py`)

`setup_logging()`:
- Uses `RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=3)`.
- Logs at INFO level, with format `time level module message`.
- Every module uses `logging.getLogger(__name__)`.
- Terminal output comes from `print_event`, not from log handlers.

---

## 11. Config (`settings.py`)

- `collect_settings(path=None, ...)`, `load_config(path=None)` and `save_config(data, path=None)` resolve `paths.config_path()` at call time.
- `save_config()` reads the existing JSON object (if any), updates only `keywords` / `locations` / `sources`, and writes it back. Other keys, such as the app's future `"app": {...}` block, are preserved.
- `load_config()` keeps ignoring unknown keys.

---

## 12. Testing

All tests use `unittest` with no network access. Existing tests must keep passing.

### `test_store.py`
- `add_jobs` returns only new jobs.
- A re-seen job keeps its status, and its `last_seen_at` is updated.
- A duplicate in the same batch is not new twice.
- `legacy_seen` blocks.
- `set_status` rejects bad values.
- `list_jobs` filters, and hides dismissed jobs by default.
- `recover_interrupted_runs`.
- `user_version == 1`.

### `test_paths.py`
- `HAWKEYE_DATA_DIR` override.
- The LinkedIn profile resolution order.

### `test_settings.py` (additions)
- `save_config` merge keeps unknown keys.
- Legacy config is copied, never moved or overwritten.

### `test_pipeline.py`
Uses fake fetchers and a fake driver factory. It checks:
- Event order and totals.
- One failing query does not stop the run.
- `LinkedInLoginRequired` skips the remaining posts queries.
- `ChromeNotFound` skips browser queries while the HTTP lane completes.
- A single crash restarts the driver; a second crash skips the rest.
- Cancel stops after the in-flight query, and the state is `cancelled`.
- `driver.quit()` is always called.
- No driver is created when no browser source is selected.
- Jobs are saved per query.
- An `on_event` exception is ignored.

### `test_catalog.py` (additions)
- `plan_queries` carries `location_id`.
- `expand_searches` output is identical to the current output for the existing test inputs.

### Parser fixture tests
- `tests/fixtures/{bayt,naukrigulf,google_jobs,linkedin_posts}.html` are real pages trimmed to ≤ 3 cards.
- **The LinkedIn posts fixture is anonymised:** names and post text are replaced with synthetic text while the DOM structure is kept, because this is a public repo.
- The JobSpy `_parse_jobs` test uses a small `pandas.DataFrame`.

### Manual checks
- `bash run.sh` end to end with the current `config.json`.
- Run it again: no new jobs reported.
- Ctrl+C mid-run.
- **Speed:** time a full run before and after the change with the same `config.json`, and record both numbers in the PR. There is no promised target.

---

## 13. Acceptance criteria

1. `python3 -m unittest discover -s tests -v` passes.
2. Terminal mode prompts, prints, and emails as before. A second identical run reports no new jobs.
3. An existing user upgrading from `main` gets no repeat notifications for jobs in their old `seen_jobs.json`, and their saved settings are reused.
4. Nothing new is written to the repo root. All runtime files live in the data directory.
5. Ctrl+C ends the run as `cancelled` within one query per lane, and Chrome is closed.
6. Before and after run times are measured and reported.
7. README, `docs/ARCHITECTURE.md`, and `CLAUDE.md` describe the data directory, the reset steps, the LinkedIn profile location, and Chrome auto-detection.

---

## 14. Risks

- **Parallel lanes hit LinkedIn twice at once** (JobSpy + logged-in posts search). Mitigated by running posts last in the browser lane. In v1 the app limits posts to once a day.
- **Smart-wait timeouts too short on slow connections.** The constants are easy to tune, and a timeout degrades to "parse what loaded", not failure.
- **Windows Chrome detection** is only exercised for real once sub-project 3 adds Windows CI.
- **Fixture rot.** Fixtures prove the parser works on a snapshot, not on today's site. The daily canary (sub-project 3) covers live breakage.
