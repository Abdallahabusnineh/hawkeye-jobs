# hawkeye-jobs — Architecture & Code Reference

Terminal job hunter. The user picks keywords, locations, and sources; it scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf and Google Jobs, then prints and emails new listings.

---

## Directory Layout

```
hawkeye-jobs/
├── src/
│   ├── main.py          # Terminal front-end: prompts, prints events, emails new jobs
│   ├── pipeline.py      # run_search(): plans queries, two lanes, saves per query, events, cancel
│   ├── store.py         # SQLite store: runs, jobs, statuses, legacy dedup import
│   ├── paths.py         # Per-OS data directory and the files inside it
│   ├── logs.py          # Rotating log file (hawkeye.log)
│   ├── settings.py      # Terminal prompts + config.json
│   ├── picker.py        # Arrow/space checkbox, searchable country list, yes/no
│   ├── catalog.py       # Country/source → per-site queries
│   ├── countries.py     # Bundled country list (data/countries.json, Arabic + English)
│   ├── filters.py       # Title / hiring-post matching
│   ├── scraper.py       # LinkedIn + Indeed via JobSpy
│   ├── bayt.py          # Bayt.com via Selenium
│   ├── naukrigulf.py    # NaukriGulf via Selenium
│   ├── google_jobs.py   # Google Jobs via Selenium
│   ├── browser.py       # Chrome discovery, version detection, driver factory, wait_for()
│   ├── utils.py         # Shared: clean_url(), is_recent()
│   ├── version.py       # __version__
│   ├── run_lock.py      # RunLock: cross-process one-search-at-a-time lock
│   ├── reporting.py     # report_pending(): notify, then mark reported
│   ├── notify.py        # DesktopNotifier
│   ├── runner.py        # RunManager + AutoSearch
│   ├── app.py           # Desktop launcher
│   ├── web/             # server.py, api.py, services.py, redact.py, static/
│   └── email_sender.py  # HTML email builder + Gmail SMTP sender
├── config.example.json  # Sample search settings
├── .env.example         # Credential template
├── run.sh               # Loads .env and runs the pipeline
├── packaging/           # hawkeye.spec, launcher.py, icon.svg/png, smoke_test.py, RELEASE_NOTES.md
├── site/                # Download page (GitHub Pages)
├── .github/workflows/   # ci.yml (tests), release.yml (builds + release), pages.yml (download page)
├── requirements.txt     # Runtime dependencies, pinned
├── requirements-dev.txt # + test client, PyInstaller, Pillow
└── CLAUDE.md
```

Runtime files live in the per-OS data directory (see src/paths.py), never in the repo: `hawkeye.db`, `config.json`, `hawkeye.log`, `linkedin-profile/`.

---

## Pipeline Flow (`src/main.py` → `src/pipeline.py`)

`main.py` does: `setup_logging()` → `migrate_legacy_config()` → open `Store` → `recover_interrupted_runs()` → `import_legacy_seen_file()` → print `Data folder: ...` → `collect_settings()` → `run_search(settings, on_event=print, store=store)` → report `store.unnotified_jobs()`: print them, email them when email is configured, and `mark_notified` them only after the email succeeds (or right after printing when email is not set up). At most `REPORT_LIMIT` (500) jobs are reported per run; when that many are pending, a "more jobs are waiting" line is printed and the rest go out next run. This also re-reports jobs left by an earlier run that was killed before reporting. The store is closed in a `finally`. Ctrl+C is handled inside `run_search` (see Cancel).

`run_search(settings, on_event, cancel, *, country_labels, store, fetchers, driver_factory) -> RunSummary(run_id, state, found, new_jobs)`:

1. `catalog.plan_queries()` lists every query (`source`, `keyword`, `location_id`, `args`), sorted in `SOURCE_ORDER`. Each gets an `index`.
2. The run is recorded (`store.start_run`) and `run_started` is emitted.
3. Two lanes, one thread each, run in parallel; queries inside a lane run one after another:
   - **HTTP lane** — `linkedin`, `indeed` (JobSpy, no browser).
   - **Browser lane** — `bayt`, `naukrigulf`, `google_jobs` (one Chrome driver for the whole lane, on a fresh temporary profile).
4. Lanes never touch the Store and never call `on_event`. They push messages on a queue. The thread that called `run_search` drains it: it sets each job's `keyword` and `location_id`, calls `store.add_jobs()` for that query (so a crash or Ctrl+C keeps everything saved so far), and emits events. `on_event` therefore always runs on the caller's thread. If `on_event` raises, the error is logged and ignored.
5. After the lanes are joined, the last request time per host group is saved (see Polite throttling), then `store.finish_run()` records the final state, then `run_finished` is emitted. If `run_search` created its own Store, it closes it.

Each lane keeps its own `seen_urls` set (cross-source duplicates within a lane); cross-run dedup is the Store's job.

### Events

Every event is a dict with a `type` key. Values are data only (never English sentences); `main.format_event()` turns them into terminal lines.

| type | fields |
|---|---|
| `run_started` | `run_id`, `total` |
| `query_started` | `index`, `source`, `keyword`, `location_id` |
| `query_done` | `index`, `source`, `keyword`, `location_id`, `found`, `new` |
| `query_failed` | `index`, `source`, `keyword`, `location_id`, `error` (exception class name) |
| `query_skipped` | `index`, `source`, `keyword`, `location_id`, `reason` `browser_unavailable` (History may also show `login_required` / `profile_in_use` from LinkedIn posts runs before 1.2.0) |
| `browser_failed` | `reason` in `chrome_not_found`, `start_failed`, `crashed` |
| `run_finished` | `run_id`, `state`, `found`, `new` |

Guarantees: `run_started` is first and `run_finished` last. A query's `query_started` precedes its `query_done` / `query_failed`; skipped queries have no `query_started`. Events from the two lanes may interleave. On cancel, lanes stop without emitting skip events.

### Failure handling

- One failed query emits `query_failed` and the lane continues.
- `ChromeNotFound` or a failed Chrome start: `browser_failed` and every browser query is skipped (`browser_unavailable`); the HTTP lane is unaffected.
- Chrome dying mid-run: the driver is restarted once; a second crash emits `browser_failed` (`crashed`) and skips the rest.
- Errors before `run_started` (e.g. the database cannot be written) raise. After it, `run_search` never raises: an unexpected error gives state `failed`.

### Polite throttling

Each lane waits on a `Throttle` (`src/throttle.py`) before every query: a random minimum gap between the **start** of consecutive requests to the same host (page-load time counts toward the gap). This protects users' accounts and IPs from bot detection (LinkedIn account restrictions, Google CAPTCHAs, Bayt/Cloudflare challenges, HTTP 429). Slower is acceptable; harm is not. The first request waits only for the gap left by a request saved from an earlier run (see Across runs); otherwise it goes immediately; the wait sleeps in slices of at most 0.5 s and stops as soon as `cancel` is set (the lane then ends as on its pre-query cancel check, with no events for that query).

- **Host groups:** throttles are keyed by `pipeline.SITE_GROUPS` (one host per source today), built once in `run_search` and shared by both lanes. Slots are reserved under a lock, so two lanes never get requests closer than the gap. If two sources ever share a host, the group uses the per-field largest (min, max) among them.
- **Back-off:** when a query fails (not on cancel), its group's gap doubles for the rest of the run, capped at 4x (`Throttle.back_off()`).
- **Across runs:** spacing persists. Before the run is recorded, `run_search` reads `store.site_last_requests()` and `prime()`s each host group's throttle with its saved Unix time, so the first request of a new run waits out the remaining gap. As each query starts, and again after the lanes are joined, it saves `throttle.last_request_wall()` per group with `store.save_site_requests()` (table `site_requests`; the time never moves backwards). The save is best effort (failures are logged, Ctrl+C is retried) and also happens when the run is cancelled or failed, since requests that were sent still count. Saving at each query start means a killed process still leaves its times behind. Lanes never touch the store; priming and saving run on the calling thread. Known limit: the times are wall-clock, so a clock that jumps forward between runs can shorten the first gap. `delays={}` throttles nothing and saves nothing.

| Source | Gap (seconds, min-max) |
|---|---|
| linkedin | 3-6 |
| indeed | 3-6 |
| bayt | 6-10 |
| naukrigulf | 4-7 |
| google_jobs | 8-12 |

Values live in `pipeline.POLITE_DELAYS`. `run_search(..., delays={...})` overrides them (tests pass `{}`); a source missing from `delays` is not throttled.

### Cancel

Pass a `threading.Event` as `cancel`, or press Ctrl+C: a `KeyboardInterrupt` on the calling thread sets the event instead of aborting. Each lane checks it before its next query, so cancel takes at most one in-flight query. Lanes are always joined (the Chrome driver is quit), the run is recorded as `cancelled`, and `run_finished` is still emitted. Jobs saved so far are kept, and `main.py` reports them (see Pipeline Flow).

---

## Desktop app

`src/app.py` is the desktop entry point. The window is a thin shell around a local web UI; nothing leaves the machine except the searches themselves.

### Launch (`src/app.py`)

- `free_port()` picks a free port on 127.0.0.1; `build(port=None)` makes a per-launch token (`secrets.token_urlsafe(32)`), the `RunManager` (with `DesktopNotifier`), `AutoSearch`, the FastAPI app and a `uvicorn.Server` (host `127.0.0.1`, `access_log=False` because the token appears in `/api/events?token=`, `lifespan="off"`). It returns an `AppParts` dataclass and starts nothing.
- `main()` runs `_prepare_data()` (legacy config import, `recover_interrupted_runs()`, legacy `seen_jobs.json` import), serves uvicorn on a daemon thread, starts `AutoSearch`, reports pending jobs once (`report_pending_now`), then opens a pywebview window on `window_url(port, token)`. `--no-window` only prints the URL (UI development, browser checks) and waits for Ctrl+C.
- `shutdown(parts, wait_seconds=35)`: stop `AutoSearch`, `manager.close()` (every later `start()` raises `RunInProgress`, so neither a tick nor `POST /runs` can start a new search), cancel the running search and wait for it (in-flight requests finish, Chrome closes; warns if it is still alive), close SSE subscribers, set `server.should_exit`. A failure in `report_pending_now()` at launch is logged and retried next launch; with `--no-window` a dead server thread exits non-zero.

### Running in the background (`src/app.py`, `src/background.py`)

Automatic search only helps while the app runs, so by default closing the window hides it and the app stays in the tray.

- **Tray icon** (`_make_tray`, pystray): menu *Open* (default), *Search now*, *Quit*, labelled in the UI language at launch. Created on the main thread before `webview.start()` (`run_detached()`; on macOS pystray shares pywebview's `NSApplication`). Its callbacks run on the GUI thread, so each starts a worker thread (`_in_thread`). If no tray can be created (some Linux desktops) `background.tray` is false, the Settings switch is disabled, and closing quits as before.
- **CloseGuard**: with `keep_running` (app setting, default on) and a tray, a close request hides the window and returns False; the first time, a notification says the app is still running (`hidden_hint`, sent off the GUI thread; `background_hint_shown` records it). `quit()` (tray *Quit*, macOS Cmd+Q) sets `quitting`; if a search runs it shows the window and stops the search with «Stopping…», otherwise it destroys the window. Closing the last window ends `webview.start()`, so `main()`'s `shutdown()` runs.
- **macOS** (`_mac_hooks`): the app delegate's `applicationShouldTerminate:` is replaced to call `guard.quit()` and return NO — letting AppKit terminate would end the process without `shutdown()` (search not stopped, locks and `instance.json` left behind). `applicationShouldHandleReopen:hasVisibleWindows:` (Dock icon, opening the app from Finder while it runs) shows the window. While hidden the app is an accessory app (no Dock icon, `_mac_dock`).
- **`--background`** starts hidden (used by the login item). macOS creates the window hidden; Windows and Linux create it minimized and hide it once shown, because a never-shown pywebview window blocks `show()` for 20 s.
- **SingleInstance** (`background.py`): `<data>/app.lock` (same OS lock as `run_lock.py`) makes the first copy the only app; it writes `instance.json` (port + token, mode 600). A second launch POSTs `/api/window/show` with that token (no proxy) and exits; with `--background` it exits quietly. `release()` deletes `instance.json`.
- **Autostart** (`background.py`): app setting `start_at_login` (default off). `PATCH /api/app-settings` applies a change to the OS (`services.set_start_at_login`); if the OS refuses, the setting is reverted and the API answers 500 `autostart_failed`. Entries: macOS `~/Library/LaunchAgents/io.github.abdallahabusnineh.hawkeyejobs.plist` (RunAtLoad), Windows `HKCU\…\Run\Hawkeye Jobs`, Linux `~/.config/autostart/hawkeye-jobs.desktop`; each runs `launch_command()` (the executable, `$APPIMAGE`, or Python + app.py from source) with `--background`. While enabled it is rewritten at every launch, in case the app moved.

### Server guards (`src/web/server.py`)

One middleware: the `Host` header must be `127.0.0.1:<port>` or `localhost:<port>` (blocks DNS rebinding), else 403. Every `/api/*` request needs the token in `X-Hawkeye-Token` (compared with `hmac.compare_digest`); only `/api/events` also accepts `?token=` because `EventSource` cannot set headers. Failures return 403 `{"error": "forbidden"}`. Everything else is the static UI (`src/web/static/`). Handlers only use `AppServices` (`services.py`), so tests inject fakes: no network, Chrome, notifications or real folders.

### RunManager / AutoSearch (`src/runner.py`)

- `RunManager.start(trigger)` allows one search at a time: an in-process lock plus `RunLock` (`src/run_lock.py`, file `<data dir>/run.lock`, non-blocking OS lock released by the OS if the holder dies) shared with terminal mode. It runs `pipeline.run_search` on a worker thread, records each finished query in `run_queries`, keeps a `current` progress snapshot, fans events out to SSE subscribers (bounded queues; a slow subscriber is dropped and the UI reconnects). Launch recovery (`recover_interrupted`) and `report_pending_now` run only while holding the run lock and are skipped if a search runs anywhere. It raises `RunInProgress` / `NoSearchSettings`.
- `exclusive()` is a context manager that refuses (`RunInProgress`) while a search runs and blocks new ones inside the block. Reset and the LinkedIn login window use it.
- `AutoSearch` ticks every 60 s and starts a `trigger="auto"` run when enabled, onboarding is done and the configured hours have passed since the last finished run. After any attempt it made (started, failed, or no settings) it backs off a full interval; when another run holds the lock it simply tries again next tick.
- `settings.py` has a `_CONFIG_LOCK` (RLock) that serialises every write to `config.json` (search settings and the `app` section).

### Reporting and notifications

`reporting.report_pending(store, notifier)` sends `unnotified_jobs()` to a notifier (`notifier(jobs) -> bool`) and marks them with `mark_notified` only when it returns True. The app's notifier is `notify.DesktopNotifier` (desktop-notifier; Arabic or English headline from app settings). It always returns True: if the OS refuses, jobs stay visible as "New" in the app rather than piling up and re-notifying. Terminal mode uses the same function with print + email.

### Schema v3

`runs` gains `trigger` (`manual` | `auto` | `terminal`) and `search_id`; the new `run_queries` table stores one row per finished query (`run_id`, `idx`, `source`, `keyword`, `location_id`, `status` in `done|failed|skipped`, `found`, `new`, `detail`). `Store.run_details(id)` joins them for the History page.

### Problem report and redaction

`POST /api/report-problem` reads the last 200 log lines, passes them through `web/redact.py` (home path and user name, e-mail addresses, `token/key/secret/password/pass` values, bearer/basic credentials, query strings of URLs), builds a prefilled GitHub issue URL and opens it in the browser; the user reviews and submits it. The URL is capped at 6000 chars by dropping the oldest log lines first.

### Front-end (`src/web/static/`)

No build step. `index.html` loads `app.js` (an ES module that registers the Alpine component `hawkeye` on `alpine:init`) and then the vendored Alpine.js 3.14.9 (`vendor/`, MIT licence, SHA-256 in `vendor/SHA256SUMS`, checked by `tests/test_frontend.py`).

- **Token:** `api.js` `takeToken()` reads `?token=` once, stores it in `sessionStorage` and removes it from the address bar, so a reload keeps working and the token never appears in a Referer. Every call sends `X-Hawkeye-Token`; only `EventSource` uses `/api/events?token=`.
- **Text:** every displayed word comes from `i18n/ar.json` / `i18n/en.json` (identical keys and placeholders; formal Arabic, checked for colloquial words). Plurals use `Intl.PluralRules` with `.one/.two/.few/.many/.other` keys. API error codes map to `error.*` keys in `logic.js` `apiErrorKey()`.
- **Live progress:** `handleEvent()` folds SSE events into `run` with `logic.applyEvent()`; on every (re)connect `onopen` re-reads `/api/state` (`fromSnapshot`), so a dropped subscriber resyncs. A query that found new jobs schedules a state and list refresh 1.5 s later.
- **Starting placeholder:** after `POST /api/runs` the UI shows a "starting" placeholder that expires after 30 s and is re-checked by a refresh.
- **Finished runs:** run ids seen in `run_finished` / `run_error` are remembered, so a late `/api/state` snapshot cannot bring a finished run's banner back (the server clears its progress only after reporting).
- **Boot:** if `app.js` cannot load, the boot screen reveals a bilingual "could not start" message after 10 s (CSS only; the CSP allows no inline scripts).
- **Safety:** scraped job fields are rendered only with `x-text`; `tests/test_frontend.py` fails on HTML sinks such as `x-html` or `innerHTML`, and on remote URLs other than the two system-browser links. Links open through `POST /api/open-url`. The server adds a Content-Security-Policy (`script-src 'self' 'unsafe-eval'` for Alpine, nothing from other origins), `Referrer-Policy: no-referrer` and `Cache-Control: no-store` to every response.
- **RTL:** `<html dir>` follows the language; CSS uses logical properties only.
- **Wizard:** shown while `onboarding_done` is false: language → Chrome check → the Search form → first search (`finishWizard()` sets `onboarding_done`, which also enables auto-search).

### Endpoints (all under `/api`, all need the token)

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/state` | 200 version, onboarding, language, theme, chrome, run, job_counts, has_settings, update, background | |
| GET | `/settings` | 200 search settings (empty defaults if unset) | |
| PUT | `/settings` | 200 cleaned settings | 422 `{errors}` |
| POST | `/estimate` | 200 `{queries, seconds}` (never fails; zeros for missing input, any body accepted) | |
| GET | `/app-settings` | 200 | |
| PATCH | `/app-settings` | 200 full settings | 422 `{errors}` (unknown or invalid keys) |
| GET | `/countries?lang=ar\|en` | 200 | 422 |
| GET | `/jobs?status&source&text&limit&offset` | 200 `{jobs, total}` (`status=all` includes dismissed) | 422 |
| PATCH | `/jobs/{id}` | 200 `{ok}` (body `{status}`) | 404 `{job: not_found}`, 422 |
| GET | `/runs?limit` | 200 `{runs}` | |
| GET | `/runs/{id}` | 200 run with its queries | 404 `{run: not_found}` |
| POST | `/runs` | 202 `{run_id}` (`null` while the run is still starting) | 409 `run_in_progress`, 422 `no_settings` |
| POST | `/runs/current/cancel` | 202 `{ok}` | 409 `not_running` |
| GET | `/events` | 200 SSE stream (keepalive every 15 s) | |
| POST | `/open-url` | 204 (http/https only; control characters refused) | 422 |
| POST | `/open-data-folder` | 204 | |
| POST | `/linkedin/login` | 202 `{ok}` (opens Chrome on the LinkedIn profile) | 409 `chrome_not_found` / `run_in_progress` |
| POST | `/report-problem` | 200 `{url}` (the backend has already opened it; the UI must not open it again) | |
| GET | `/update` | 200 `{current, latest, checked, available}` (cached 24 h; `?force=1` — the Settings button — asks GitHub again unless the last check was under a minute ago; `disabled` if the check is off) | |
| POST | `/reset` | 204 (body `{"confirm": "DELETE"}`) | 422 `confirm`, 409 `run_in_progress` / `outside_data_dir` |

---

## Source Files

### `src/settings.py` — Terminal prompts + saved config

`collect_settings()` loads `config.json` if present and shows a Yes/No picker (`↑↓` + enter). Yes reuses it. No (or no saved file) prompts for keywords (typed), then a searchable country list (`type` to filter, `↑↓` move, space select, `*` all visible, enter confirm), then **Confirm these countries: …?** (`Yes, continue` / `No, choose again`). Same confirm step after website checkboxes (`↑↓` move, space select, `a` all, `d` remove all, enter confirm). Writes `config.json`. If stdin is not a TTY, it falls back to numbered lists and `[Y/n]` confirms.

Country names come from `src/data/countries.json`, bundled with the app (no network): the 193 UN member states plus the Vatican, Palestine, Hong Kong and Macau, each with its common English and Arabic name. It is built from [mledoze/countries](https://github.com/mledoze/countries) (pinned commit, ODbL 1.0 — `src/data/COUNTRIES-LICENSE.md`) by `tools/update_countries.py`, which also applies reviewed spelling fixes (`NAME_FIXES`: المكسيك، صربيا، أوزبكستان، جمهورية الكونغو الديمقراطية …). Territories, uninhabited islands and disputed regions (Western Sahara, Taiwan, Kosovo) are deliberately not listed. `load_countries()` returns Remote plus the list in English; `localized_countries(lang, base)` relabels it in Arabic or English. `GET /api/settings` drops a saved location that is no longer listed, so a search saved by an older version still saves. To change the list, edit the script and rerun it; review the diff.

`config.json` shape:

```json
{
  "keywords": ["Flutter Developer"],
  "locations": ["jo", "ae"],
  "sources": ["linkedin", "indeed"]
}
```

Older configs that stored `jordan` / `uae` still work (`LEGACY_IDS` in `catalog.py`).

### `src/catalog.py` — Expand user choices into site queries

`get_location(id)` maps a country id (`jo`, `gb`, `remote`, …) to per-site strings/slugs. `OVERRIDES` holds Indeed/Bayt/NaukriGulf aliases. Sources that do not support a country (Bayt/NaukriGulf outside the Gulf, Indeed for Remote) are skipped. Unknown ISO codes still search LinkedIn, Indeed, and Google Jobs.

`plan_queries(keywords, location_ids, source_ids, country_labels=None)` is what the pipeline uses: a flat list of `{source, keyword, location_id, args}`, where `args` is exactly what that source's `fetch_*` takes. `expand_searches(...)` groups the same `args` per source:

```python
{
  "linkedin": [{"term", "location"}, ...],
  "indeed": [{"term", "location", "country"}, ...],
  "bayt": [(term_slug, country_slug), ...],
  "naukrigulf": [(keyword, location_slug), ...],
  "google_jobs": [(term, location), ...],
}
```

### `src/filters.py` — Keyword matching

`keyword_stems()` drops generic role words (`developer`, `engineer`, …). `is_relevant_job(title, keywords)` keeps a title if a stem matches as a whole word (`mobile` will not match `automobile`). `is_hiring_post(text, keywords)` additionally requires a hiring signal.

### `src/scraper.py` — LinkedIn + Indeed

Uses the `python-jobspy` library. No browser required. Queries come from `catalog.plan_queries()`, not hardcoded.

**`_parse_jobs(jobs, seen_urls, location, keywords) -> list`**

- `clean_url()` before dedup
- `is_relevant_job()` for title filtering
- `is_recent()` for date filtering

**`fetch_linkedin(args, seen_urls, keywords)` / `fetch_indeed(...)`**

Each runs ONE JobSpy search (`hours_old=168`) and raises on error. `pipeline.py` loops over the queries, so one failed search does not abort the run.

---

### `src/browser.py` — Chrome discovery + shared driver

```python
def find_chrome() -> str | None
def wait_for(driver, css, timeout) -> bool
def create_driver(headless=False, user_data_dir=None) -> uc.Chrome   # raises ChromeNotFound
```

- **`find_chrome()`** checks the macOS app path, the Windows `Program Files` / `LOCALAPPDATA` folders, then `google-chrome`, `google-chrome-stable`, `chrome` on `PATH`.
- **Version:** the installed Chrome major version is detected at runtime (Windows: registry `BLBeacon`; elsewhere: `chrome --version`) and passed as `version_main`, so there is no hardcoded version. Set `HAWKEYE_CHROME_MAJOR` only if detection is wrong. If nothing is detected, undetected-chromedriver resolves it itself.
- **`ChromeNotFound`** is raised by `create_driver()` when Chrome is missing; the pipeline turns it into a `browser_failed` event.
- **`wait_for(driver, css, timeout)`** polls (0.25 s) until an element matching `css` exists. Returns `False` on timeout; the caller parses whatever loaded. Scrapers use it instead of fixed sleeps.
- `user_data_dir` points Chrome at a persistent profile (`--user-data-dir`), used for the LinkedIn login.
- `--disable-blink-features=AutomationControlled` hides the Selenium signature. `headless=False` is **required**: Bayt.com's Cloudflare blocks headless Chrome.

`pipeline.py` creates one driver per run (browser lane) and quits it in a `finally`.

---


**Fallback: `src/cdp_browser.py` (`CdpDriver`).** `create_driver()` tries undetected-chromedriver first (unchanged, the tested path). If it cannot start — Windows Smart App Control blocks the patched chromedriver as an unsigned file ("An Application Control policy has blocked this file"), or the chromedriver download fails — it logs a warning and starts Chrome itself with `--remote-debugging-port` and drives it over the DevTools WebSocket (`websocket-client`, a Selenium dependency), so no extra executable runs. `CdpDriver` implements only the WebDriver subset the scrapers use (`get`, `page_source`, `title`, `current_url`, `execute_script`, `window_handles`, `switch_to.window`, `close`, `quit`, `set_page_load_timeout`) plus `wait_css()`, which `wait_for()` uses. It never sends `Runtime.enable`/`Page.enable` and Chrome runs without `--enable-automation`, so `navigator.webdriver` is false. Measured side by side on the same searches (Bayt UAE/Jordan, NaukriGulf UAE, Google Jobs UAE/Jordan), both paths returned the same job counts with no block pages. `HAWKEYE_BROWSER=cdp` forces the fallback for testing.
### `src/bayt.py` — Bayt.com Scraper

Covers: Jordan, UAE, Saudi Arabia, Kuwait, Qatar, Oman, Bahrain.

**URL pattern:**
```
https://www.bayt.com/en/{country_slug}/jobs/{term_slug}-jobs/?options%5Bsort%5D%5B%5D=d
```
Example: `https://www.bayt.com/en/uae/jobs/flutter-developer-jobs/?options%5Bsort%5D%5B%5D=d`

The `options[sort][]=d` query sorts by date, newest first. Without it Bayt sorts by relevance and page 1 is weeks old, so the age filter drops everything.

**HTML selectors (as of 2026):**
| Data | Selector |
|------|----------|
| Job card | `li[data-job-id]` |
| Title + URL | `.jb-title a, h2 a, h3 a` |
| Company | `a.t-default.t-bold` |
| Location | `.job-company-location-wrapper .t-mute span` |
| Date posted | `[data-automation-id='job-active-date']` |

**Cloudflare bypass:** `wait_for(driver, CARD_SELECTOR, 20)` waits up to 20 s for job cards while the Cloudflare JS challenge resolves. Must use `headless=False`. If no cards appear it logs a warning (with the page title) and parses what loaded.

**Entry point:** `fetch_bayt(driver, args, seen_urls, keywords)` runs ONE query (`args = (term_slug, country_slug)`) and raises on error; the pipeline decides what to do.

**`_ensure_window(driver)`** — closes any extra browser tabs opened during navigation, switches back to tab 0. Called before and after each `driver.get()`.

**`_parse_bayt_page(html, seen_urls, location, keywords)`** — parses raw HTML with BeautifulSoup. Applies `is_relevant_job()` and `is_recent()`.

---

### `src/naukrigulf.py` — NaukriGulf Scraper

Covers: Gulf-wide, UAE, Saudi Arabia, Kuwait, Qatar, Bahrain.

**URL pattern:**
```python
def _build_url(keyword, location_slug):
    kw = keyword.replace(" ", "-")
    if location_slug not in ("gulf-region", ""):
        return f"https://www.naukrigulf.com/{kw}-jobs-in-{location_slug}?sort=date"
    return f"https://www.naukrigulf.com/{kw}-jobs?sort=date"
```
Example: `https://www.naukrigulf.com/flutter-developer-jobs-in-uae?sort=date`

`?sort=date` sorts newest first; the default sorts by relevance and fills the page with old jobs.

**HTML selectors (as of 2026):**
| Data | Selector |
|------|----------|
| Job card | `.ng-box.srp-tuple` |
| Link + title container | `a.info-position` |
| Title text | `.designation-title` (inside the link) |
| Company | `a.info-org` |
| Location | `li.info-loc span:last-child` |
| Date posted | `span.time` |

**Wait:** `wait_for(driver, CARD_SELECTOR, 15)` — NaukriGulf is an Angular SPA and needs time to render. A timeout logs a warning.

**Entry point:** `fetch_naukrigulf(driver, args, seen_urls, keywords)` runs ONE query (`args = (keyword, location_slug)`) and raises on error.

**`_ensure_main_window(driver)`** — NaukriGulf opens extra tabs on some navigations. This helper closes all tabs except the first and switches back to it. Called before and after each `driver.get()`.

---

### `src/google_jobs.py` — Google Jobs Scraper

Covers: Gulf, Europe, Americas, Remote/Worldwide.

**URL pattern:**
```python
def _build_url(term, location):
    q = f"{term} jobs {location}".strip().replace(" ", "+")
    return f"https://www.google.com/search?q={q}&ibp=htl;jobs&hl=en"
```
`ibp=htl;jobs` activates the Google Jobs panel.

**How extraction works:**

All 10 job cards and their apply links are embedded in the initial HTML — no clicking required. The page is parsed by zipping parallel element lists by index:

```python
titles    = soup.select(".PUpOsf")         # job titles (10 elements)
companies = soup.select(".a3jPc")          # company names
locations = soup.select(".FqK3wc")         # location text
link_grps = soup.select('[role="list"].EDblX')  # apply link groups (10 lists)
```

Each `link_grps[i]` contains multiple `<a>` tags (one per platform hosting the job). The scraper takes the first absolute non-Google URL (old format); if there is none it falls back to the first Google redirect link `href="/goto?url=CAES..."` (current format), stored as `https://www.google.com/goto?url=CAES...`. The `/goto` token is stable per job across page loads, so dedup still works. If the anchor has `title="Apply on X"`, the description gets a ` (via X)` suffix.

**Google domains skipped:**
```python
SKIP_DOMAINS = ["google.com", "google.jo", "google.ae", "google.co", "accounts.google", "goo.gl"]
```

**Note:** Google Jobs doesn't expose a `date_posted` field in its HTML, so `published` is set to `""`. Jobs are included regardless of age (no age filter here — Google Jobs already surfaces recent jobs).

**Wait:** `wait_for(driver, CARD_SELECTOR, 15)` (`.PUpOsf`) for the jobs panel to render. A timeout logs a warning.

**Entry point:** `fetch_google_jobs(driver, args, seen_urls, keywords)` runs ONE query (`args = (term, location)`) and raises on error.

---

### LinkedIn hiring posts — removed in 1.2.0

Versions up to 1.1.5 also searched LinkedIn's content search for hiring posts ("We're hiring a Flutter developer"). It was removed because it was the one source that ran on the user's **own logged-in LinkedIn account** (a restriction risk for that account, against the project's no-harm rule), it needed a separate login step that confused users (a Chrome window left open blocked every search), and LinkedIn changed the page twice in one day (October 2026: new markup without activity URNs or post links, then markup without any stable attributes from the second search of a session). What remains for compatibility: `catalog.REMOVED_SOURCES` (a saved search naming `linkedin_posts` has it dropped in `settings.load_config()`), the `source.linkedin_posts` labels and `JOB_SOURCE_IDS["linkedin post"]` so jobs saved by older versions still display, the `history.q.skipped.login_required` / `profile_in_use` texts for old History entries, and `app._remove_old_linkedin_profile()`, which deletes the app's old `<data dir>/linkedin-profile` (a logged-in Chrome profile) at launch — never `LINKEDIN_PROFILE_DIR` or `~/.hawkeye-linkedin-profile`, which users created themselves. An old `last_posts_run` app setting is ignored and dropped on the next save.

### `src/utils.py` — Shared Utilities

**`clean_url(url: str) -> str`**

Strips UTM and tracking query parameters so the same job from different sources maps to the same canonical URL for deduplication.

Strips: `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, `source`, `ref`, `referer`, `tracking`

Example: `https://indeed.com/viewjob?jk=abc123&utm_source=google` → `https://indeed.com/viewjob?jk=abc123`

**`is_recent(date_str: str, today=None, max_days=None) -> bool`**

Returns `True` if the job was posted within N days, where N (`max_days`, default `job_age_days()`) is the user's **job age** setting: 7, 14 (default) or 30 (`JOB_AGE_CHOICES`, `DEFAULT_JOB_AGE_DAYS`; app setting `job_age_days`, chosen on the Search page). `run_search(..., job_age_days=)` calls `set_job_age_days()` at the start of each run; the setting is module-level because one search runs at a time per process. JobSpy gets `hours_old = job_age_days() * 24`. It applies to LinkedIn jobs, Indeed, Bayt and NaukriGulf; Google Jobs shows no dates. Why 14 by default: Bayt and NaukriGulf post far less often than LinkedIn and most of their open jobs are 1–4 weeks old, so a 7-day window often showed nothing from them; dedup means a wider window only adds to the first search. `today` (a date) is injectable for tests; dates are compared as UTC calendar dates.

Handles multiple formats:
| Input format | Example | Behavior |
|---|---|---|
| Empty / None / NaN | `""`, `"none"`, `"nan"` | `True` (benefit of the doubt) |
| Immediate | `"just now"`, `"today"`, `"moments ago"` | `True` |
| Hours ago | `"3 hours ago"` | `True` if ≤ N x 24 |
| Days ago | `"1 day ago"`, `"7 days ago"` | `True` if ≤ N |
| Weeks ago | `"2 weeks ago"` | `True` if weeks x 7 ≤ N |
| Months ago | `"1 month ago"` | `True` if months x 30 ≤ N (so only with N = 30) |
| Yesterday | `"yesterday"` | `True` |
| Old markers | `"30+ days ago"`, `"1 year ago"` | `False` |
| Day-month | `"29 Apr"`, `"1 May"` | Calendar dates, `True` if ≤ N days (future dates roll back a year) |
| ISO date | `"2026-05-04"` | Calendar dates, `True` if ≤ N days (a time part is ignored) |
| Unparseable | anything else | `True` |

---

### `src/store.py` — SQLite store (jobs, statuses, runs)

Replaces the old `tracker.py` / `seen_jobs.json`. Keeps every job found, so history and statuses can be shown. The database is `paths.db_path()` (`hawkeye.db`).

**Tables** (`PRAGMA user_version` = `SCHEMA_VERSION`, created on first open):
- `runs` — one row per search: `started_at`, `finished_at`, `state`, `settings_json`, `found_count`, `new_count`. States: `running`, `done`, `cancelled`, `failed`.
- `jobs` — one row per unique URL. `id` = `job_id(url)` = MD5 of `clean_url(url).strip().rstrip("/")` (same as the old tracker). Columns: `title`, `url`, `description`, `source`, `location`, `location_id`, `keyword`, `published`, `status`, `status_changed_at`, `first_seen_at`, `last_seen_at`, `first_run_id`, `notified_at` (NULL until the job has been reported; schema v2 stamps jobs that existed before the upgrade with `first_seen_at`).
- `site_requests` — `site` (host group, e.g. `bayt.com`) and `last_request_at` (Unix seconds of the last request start); used to keep polite spacing between runs.
- `legacy_seen` — hashes imported from the old `seen_jobs.json`; used for dedup only.

**Statuses:** `new`, `saved`, `applied`, `dismissed` (`set_status`; `list_jobs` hides dismissed unless asked).

**`add_jobs(run_id, jobs) -> list`** is called once per query. A job whose id is in `legacy_seen` is skipped. A job already in `jobs` only gets `last_seen_at` updated. Otherwise it is inserted with status `new`. It returns only the genuinely new jobs (each with an `id`); the terminal does not use that list but reports `unnotified_jobs()` (see Reporting).

**Reporting:** a job is marked seen as soon as its query finishes, but reported separately. `unnotified_jobs(limit=500)` returns jobs with `notified_at IS NULL` and status not `dismissed`, oldest first; `mark_notified(ids)` stamps them (idempotent, returns rows changed). `site_last_requests()` / `save_site_requests(times)` read and upsert `site_requests` (an upsert never moves a time backwards).

**Legacy import:** `import_legacy_seen_file(path)` reads `seen_jobs.json` into `legacy_seen`; it is safe to call every start and never modifies or deletes the file. `settings.migrate_legacy_config()` likewise copies the repo-root `config.json` into the data dir once.

**Threading:** one `Store` per thread (sqlite3 connections are not shared). WAL mode lets a reader work while a search writes. In a run only the thread calling `run_search` uses the Store. `recover_interrupted_runs()` marks leftover `running` runs as `failed`; call it once at process start, never during a search.

**Resetting:** delete `hawkeye.db*` (`hawkeye.db`, `hawkeye.db-wal`, `hawkeye.db-shm`; the last two remain after an unclean exit) from the data directory. If a repo-root `seen_jobs.json` exists, delete it too: `main.py` re-imports it into `legacy_seen` on every start, so its jobs would stay suppressed.

---

### `src/email_sender.py` — Email Builder + Sender

Sends an HTML email via Gmail SMTP SSL (port 465).

**Credentials (from environment variables):**
| Variable | Purpose |
|---|---|
| `GMAIL_USER` | Sender Gmail address |
| `GMAIL_PASS` | Gmail App Password (not account password) |
| `RECIPIENT_EMAIL` | Recipient address (defaults to `GMAIL_USER`) |

**`_build_html(jobs) -> str`**

Builds an HTML email with:
- Dark gradient header with hawk emoji and job count
- Source summary pills (colored by platform)
- One card per job with: title (clickable link), company + location, source badge, "Apply Now →" link, date found timestamp

**Source colors:**
| Source | Color |
|---|---|
| LinkedIn | `#0A66C2` (LinkedIn blue) |
| Indeed | `#003A9B` (Indeed dark blue) |
| Google Jobs | `#4285F4` (Google blue) |
| Bayt | `#E8A020` (amber) |
| NaukriGulf | `#FF6B35` (orange) |
| Glassdoor | `#0CAA41` (green) |

**`email_configured() -> bool`** is true when `GMAIL_USER` and `GMAIL_PASS` are set.

**`send_email(jobs: list) -> bool`**

Sends both `text/plain` (fallback) and `text/html` parts. Returns `True` when sent (or when `jobs` is empty), `False` when not configured or SMTP fails; `main.py` marks jobs notified only on `True`.

---

## Job Dict Schema

All scrapers produce job dicts with this shape:

```python
{
    "title":       str,   # Job title as scraped
    "url":         str,   # Direct apply URL (UTM-cleaned before dedup)
    "description": str,   # "{company} - {location}" or just location
    "source":      str,   # "Linkedin", "Indeed", "Bayt", "NaukriGulf", "Google Jobs"
    "keyword":     str,   # The user keyword that found the job (set by the pipeline)
    "location_id": str,   # Normalised country id of the query (e.g. "jo")
    "location":    str,   # Search location (e.g. "United Arab Emirates")
    "published":   str,   # Raw date string from the source
    "date_found":  str,   # UTC timestamp when the scraper ran
}
```

---

## Search Coverage

Locations are chosen from the world list in the terminal. Per-source support:

| Source | Supported locations |
|---|---|
| LinkedIn | Any country from the API, plus Remote |
| Indeed | Any country from the API (not Remote) |
| Bayt | Jordan + Gulf only (`OVERRIDES` slugs) |
| NaukriGulf | Gulf only (`OVERRIDES` slugs) |
| Google Jobs | Any country from the API, plus Remote |

---

## Common Maintenance Tasks

### Add a country or source mapping
Country names come from REST Countries. Site-specific slugs (Bayt/NaukriGulf/Indeed aliases) live in `OVERRIDES` in `src/catalog.py`. Add a source in `SOURCES` / `SOURCE_ORDER`.

### Tune title matching
Edit `keyword_stems()` stopwords or `is_hiring_post()` signals in `src/filters.py`.

### Fix broken selectors
Bayt and NaukriGulf change their HTML periodically. If a scraper returns 0 jobs:
1. Open the site in a browser
2. Inspect a job card's HTML
3. Update the `soup.select(...)` calls in the relevant `_parse_*_page()` function
4. The `CARD_SELECTOR` constant in each scraper must match the job-card selector the parser uses (it is what `wait_for` waits on).

### Google Jobs returns 0
If `.PUpOsf` or `[role="list"].EDblX` selectors stop working:
1. Open a Google Jobs URL for any keyword in Chrome
2. Inspect the job title elements and the apply-link list containers
3. Update the selectors in `src/google_jobs.py` → `_parse_google_page()`

---

## Dependencies

`requirements.txt` pins every runtime dependency (exact versions), so each release is built from the same code: python-jobspy (LinkedIn + Indeed), pandas, requests, beautifulsoup4, undetected-chromedriver + selenium (Chrome sources), setuptools (distutils for undetected-chromedriver), platformdirs, fastapi + uvicorn (local API), pywebview (window; on Linux with the Qt extra), desktop-notifier. `requirements-dev.txt` adds httpx2 (FastAPI test client), PyInstaller and Pillow (icon conversion).

Python 3.12: python-jobspy pins `numpy==1.26.3`, which has wheels only up to 3.12. Releases and CI use 3.12.

Install: `pip install -r requirements-dev.txt`

## Packaging and releases

- `packaging/hawkeye.spec` — PyInstaller, one-folder build (faster start, fewer antivirus false alarms than one-file). Bundles `src/web/static` as `web/static`, `src/data/countries.json` as `data/`, the tray icon, and the packages with data files or dynamic imports (`collect_all`: jobspy, tls_client, webview, desktop_notifier, undetected_chromedriver, selenium; all uvicorn submodules; winrt on Windows). tls_client ships its Go library for every OS; the spec keeps only this OS's files, because the others are not valid binaries there and stop the build. macOS gets an ad-hoc-signed `.app` bundle (`io.github.abdallahabusnineh.hawkeyejobs`).
- `packaging/launcher.py` — the frozen entry point. It calls `multiprocessing.freeze_support()` before importing anything: helper processes (multiprocessing's resource tracker) re-run the executable, and without this each one imported the app and started another helper.
- `app.py` in a packaged build (`app.FROZEN`): replaces missing console streams with devnull (windowed builds have none; uvicorn writes to them) and skips the repo-folder migration of a terminal-era `config.json` / `seen_jobs.json`. `--self-test` checks the bundled data and lazily imported modules (country list ≥ 200 with Arabic names, scrapers, tls_client, webview, window files) and exits 0/1; it makes no network requests.
- `packaging/smoke_test.py` — runs `--self-test`, then starts `--no-window` on a free port and checks `/`, `/i18n/ar.json`, `/vendor/alpine.min.js` (200) and `/api/state` without the token (403).
- `.github/workflows/release.yml` — on a `v*` tag (or by hand): tests on Linux/Windows/macOS, then builds on macos-latest (Apple Silicon), macos-15-intel, windows-latest and ubuntu-22.04 (older glibc, so the Linux build runs on more distributions), runs the smoke test, packages (`.dmg` with an Applications link, `.zip`, `.tar.gz`), and on a tag publishes a GitHub release with `SHA256SUMS.txt` and `packaging/RELEASE_NOTES.md`. Asset names have no version (`HawkeyeJobs-Windows.zip`, …) so `releases/latest/download/<name>` links on the download page never change. The tag must equal `v` + `src/version.py`.
- Windows also gets `HawkeyeJobs-Windows-Setup.exe` from `packaging/windows/hawkeye.iss` (Inno Setup): a per-user install into `%LOCALAPPDATA%\Programs\Hawkeye Jobs` (no administrator prompt), Start-menu and optional desktop shortcuts, an uninstaller, and a fixed `AppId` so a newer installer upgrades in place. CI installs it silently and smoke-tests the installed app.
- **Microsoft Store (MSIX)** — the free way past Windows Smart App Control, which blocks every unsigned file: the Store re-signs the package with Microsoft's certificate. `packaging/windows/build_msix.py` copies the PyInstaller folder, writes `packaging/windows/msix/AppxManifest.xml` (identity from Partner Center: `abdallahabusnineh.HawkeyeJobs`, publisher `CN=C6E75268-…`, product `9PB9XTPR0XH4`; version `x.y.z.0`, the Store's format), draws the logos from `packaging/icon.png` (scale and target-size variants, indexed by `makepri` from a folder holding only the manifest and `Assets/` — makepri reads library folders named like `en` as language qualifiers) and packs with `makeappx`. The package is unsigned and is **not** a release asset: the workflow uploads it as the `microsoft-store` artifact, to be uploaded in Partner Center. `packaging/windows/test_msix.ps1` signs a copy with a throwaway certificate (trusted on the runner only, removed afterwards), installs it, and runs the smoke test through the `hawkeye-jobs.exe` execution alias with `HAWKEYE_EXPECT_PACKAGED=1`, so `--self-test` also checks that it runs as the package, that its data folder is writable and that the StartupTask answers.
- Inside the package (`src/packaged.py`, detected with `GetCurrentPackageFamilyName`): the data folder is `%LOCALAPPDATA%\Packages\<family>\LocalState` (a package's writes to `%LOCALAPPDATA%` are redirected, so File Explorer would not see them; Windows deletes LocalState at uninstall), and the first launch copies the database (SQLite backup API) and `config.json` of an earlier GitHub install. Start at login uses the manifest's StartupTask through WinRT (`winrt-Windows.ApplicationModel`), because registry writes never reach the real Run key; it cannot pass `--background`, so `app.main` asks `AppInstance.GetActivatedEventArgs()` whether the StartupTask started it. If the user turned it off in Windows Settings only they can turn it on there: the API answers `autostart_blocked`. Updates come from the Store: `/api/update` never asks GitHub and Settings shows "Updates arrive automatically from the Microsoft Store". The Store listing needs a privacy policy URL: `site/privacy.html`.
- Linux also gets `HawkeyeJobs-Linux.AppImage` (`packaging/linux/build_appimage.sh`: the PyInstaller folder under `usr/lib/hawkeye-jobs`, `AppRun`, `.desktop`, icon; appimagetool runs with `APPIMAGE_EXTRACT_AND_RUN=1` because runners have no FUSE). CI smoke-tests the AppImage itself.
- One-command installers, served from the download page: `site/install.sh` (macOS: `curl … | bash`) and `site/install.ps1` (Windows: `irm … | iex`). They download the latest release, verify it against `SHA256SUMS.txt`, install (Applications / silent per-user setup) and open the app. Files downloaded by curl or PowerShell are not marked as downloaded from the internet, so Gatekeeper / SmartScreen do not block the unsigned app.
- **macOS signing and notarization** (release tags only): the build job for the two macOS targets runs in the protected GitHub environment `release-signing` — its secrets exist only there, every run waits for the owner's approval, and only `v*` tags may deploy to it; pull requests and manual builds never get them. The job imports the Developer ID certificate (`MACOS_CERT_P12`, base64, + `MACOS_CERT_PASSWORD`) into a throwaway keychain (random password, deleted at the end), sets `MACOS_SIGN_IDENTITY` so `hawkeye.spec` signs every binary with the Hardened Runtime and `packaging/macos/entitlements.plist` (allow unsigned executable memory, disable library validation — needed by the bundled Python, ctypes/cffi and pyobjc), notarizes the zipped app with `packaging/macos/notarize.sh` (`notarytool`, `APPLE_ID` + app-specific `APPLE_APP_PASSWORD`, team 5QK2GF6VU8), staples it, builds the .dmg, signs, notarizes and staples the .dmg, and checks both with `spctl`. A tag build without the secrets fails rather than publish an unsigned Mac release. All actions are pinned to commit SHAs. Locally: `MACOS_SIGN_IDENTITY="Developer ID Application: …" pyinstaller packaging/hawkeye.spec`.
- The Windows and Linux builds are not signed (no free certificate fits the project: SignPath's terms exclude bot-detection evasion, which undetected-chromedriver and tls_client do). macOS shows "cannot verify the developer" once (System Settings → Privacy & Security → Open Anyway); Windows SmartScreen shows "More info → Run anyway". Both are explained in the release notes and on the download page.
- `site/index.html` — the download page (no trackers, no external files). It picks the button for the visitor's system from the user agent; Arabic first, English toggle.

---

## Running Locally

```bash
venv/bin/python3 src/app.py   # desktop app; --no-window prints the URL and serves only
```

Terminal mode:

```bash
cp .env.example .env   # fill in GMAIL_USER / GMAIL_PASS / RECIPIENT_EMAIL
bash run.sh            # prompts for keywords, locations, sources
```

To change saved searches: pick **No** on the reuse menu, or delete `config.json` from the data directory. Tests: `venv/bin/python3 -m unittest discover -s tests -v`.
