# Desktop app (UI) — design spec

**Status:** draft for review · **Date:** 2026-10-10 · **Sub-project:** 2 of 4 (Phase 1)
**Builds on:** `2026-10-10-core-pipeline-design.md` (merged to `main`), plus the follow-ups (schema v2, reporting, per-site throttling with persisted spacing, 7-day window, source fixes).
**Approved mockups:** `docs/superpowers/specs/mockups/2026-10-10-app-ui/` (HTML fragments from the brainstorm; open in a browser for reference).

---

## 1. Goal and constraints

Give job seekers who cannot use a terminal a desktop app for macOS, Windows and Linux, on top of the existing core.

**Hard constraints (owner decisions):**
- **Everything runs on the user's machine.** There is no project server, no account, no telemetry. The only network traffic is:
  - the job searches themselves;
  - a daily update check against the public GitHub Releases page (on by default);
  - a browser page the user opens to report a problem (nothing is sent automatically).
- **Nobody may be harmed:**
  - keep the polite per-site throttling;
  - never run two searches at once;
  - never lose a job notification;
  - never touch real user files in tests.
- **UI text is formal Modern Standard Arabic (فصحى) and English. No colloquial wording.** Arabic is right-to-left.
- **The product name is "Hawkeye Jobs" in both languages** (no Arabic name).

**v1 scope:**
- One saved search, but the data model is ready for several.
- Pages: Jobs, Search, History, Settings, plus a first-run wizard.
- Live progress, with stop.
- Job statuses.
- Auto-search every N hours while the app is open, with desktop notifications.
- Chrome detection.
- LinkedIn login button, with an account-risk warning. LinkedIn posts are off by default and run at most once per 24 h.
- Update check.
- "Report a problem".

**Not in v1:** in-app email setup, start-at-login / tray, multiple saved searches in the UI, cross-site duplicate merging, packaging (sub-project 3).

---

## 2. UX (approved in the brainstorm)

**Layout:**
- Top bar: the "Hawkeye Jobs" name and an "ابدأ البحث / Start search" button, which becomes "إيقاف البحث / Stop search" while running.
- Right-side navigation in RTL (left in LTR): **الوظائف / Jobs**, **البحث / Search**, **السجل / History**, **الإعدادات / Settings**.

**Jobs page:**
- Status filter chips: جديدة / New, محفوظة / Saved, تقدّمت / Applied, الكل / All. "Not interested" jobs are hidden except under All.
- A text search box.
- One **card per job**, showing:
  - the title;
  - company and location;
  - a source badge;
  - relative age;
  - always-visible buttons: «فتح الإعلان / Open posting», «حفظ / Save», «تقدّمت / Applied», «غير مهتم / Not interested».
- "Open posting" opens the URL in the system browser.

**Progress while searching:**
- A banner at the top of the Jobs page shows a progress bar, "36 of 80", the current site, keyword and country, and the count of new jobs so far.
- New jobs appear in the list as they are found, so the user can keep browsing.

**Search page:** four numbered sections.
1. Job titles: tag input, with a hint that boards mostly use English titles.
2. Where: country tags with search, plus Remote; names in the UI language.
3. Which sites: a checkbox per source with a one-line coverage hint.
   - LinkedIn posts are unchecked by default.
   - They come with an amber warning about the account risk and a «تسجيل الدخول إلى LinkedIn» button.
4. Auto-search: a toggle and an interval. The default is on, every 6 hours, while the app is open, with a notification on new jobs.

The page ends with «حفظ وابدأ البحث / Save and search», «حفظ فقط / Save only», and an estimated duration.

**History page:**
- A list of runs: time, manual or auto, state, found and new counts.
- Selecting a run shows each site's outcome in plain language.

**Settings page:**
- Language and theme (light, dark, auto).
- Chrome status.
- LinkedIn login.
- Version and «التحقق من التحديثات».
- «الإبلاغ عن مشكلة».
- «فتح مجلّد البيانات».
- «حذف جميع بياناتي», which needs explicit confirmation.

**First run:** a four-step wizard.
1. Language.
2. Chrome check. If Chrome is missing, explain that only LinkedIn and Indeed will be used and offer a download link.
3. The search setup (the same component as the Search page).
4. The first search.

**Messages:** every error has a فصحى and English message plus one clear action (see §7). No stack traces ever reach the UI.

---

## 3. Architecture

`app.py` starts a local FastAPI server on `127.0.0.1` (random free port) in a background thread, then opens a pywebview window at `http://127.0.0.1:<port>/?token=<random>`. The frontend is static HTML, CSS and JS using Alpine.js, vendored with no build step. Live updates use Server-Sent Events.

```
src/
  reporting.py     report_pending(store, notifier) — shared "report then mark"; used by main.py (email) and the app (desktop notification)
  runner.py        RunManager — single-run guard (in-process lock + data-dir file lock), start/cancel, event fan-out, run_queries recording, auto-search scheduler, LinkedIn-posts 24 h gate
  notify.py        desktop notifications (desktop-notifier); silent fallback when unavailable or denied
  app.py           entry point: data-dir setup, logging, legacy migration, recover runs, start server thread, open pywebview window, graceful shutdown
  web/
    server.py      FastAPI factory: token + Host checks, static files, injects nothing into HTML
    api.py         the endpoints in §4
    static/
      index.html   shell: top bar, side nav, page containers, banner
      app.js       Alpine state/components for the 4 pages + wizard
      api.js       fetch wrapper (adds X-Hawkeye-Token) and the EventSource subscription
      i18n/ar.json i18n/en.json
      vendor/alpine.min.js   (pinned version, license file alongside)
      styles.css   colour tokens, light/dark, RTL via [dir], responsive down to 360 px wide
  main.py          terminal mode switches to reporting.report_pending (email), unchanged behaviour
```

**New dependencies:**
- `fastapi`
- `uvicorn`
- `pywebview`
- `desktop-notifier`
- `babel`, for localized country names offline (CLDR)

**Config:** search settings stay in `config.json` (`keywords`, `locations`, `sources`). App settings go under `"app"`:

```json
{ "language": "ar", "theme": "auto",
  "auto_search": {"enabled": true, "hours": 6},
  "onboarding_done": false, "update_check": true,
  "last_update_check": null, "last_posts_run": null }
```

`save_config` already preserves unknown keys. A new `settings.load_app_settings()` / `save_app_settings(patch)` pair reads and merges the `"app"` block, with defaults.

**Schema v3** (same race-safe step-wise migration as v2):
- New table `run_queries(run_id, idx, source, keyword, location_id, status TEXT CHECK IN ('done','failed','skipped'), found, new, error_or_reason)`, primary key `(run_id, idx)`.
- `runs` gains `trigger TEXT NOT NULL DEFAULT 'manual' CHECK IN ('manual','auto','terminal')` and `search_id INTEGER NOT NULL DEFAULT 1`. `search_id` is the hook for multiple saved searches later. v1 always writes 1.
- Store methods:
  - `start_run(settings, trigger='manual', search_id=1)` (backward compatible);
  - `record_query(run_id, event)`;
  - `run_details(run_id)`;
  - `job_counts() -> {status: n}`;
  - `last_finished_run(trigger=None)`.

**Stores and threads:** SQLite connections are per-thread. API handlers open a short-lived `Store` per request (cheap; WAL lets them read while a run writes). The run worker thread owns its own `Store` for the whole run.

**Single-run guard:**
- `RunManager` holds a `threading.Lock`.
- It also holds an OS-level exclusive lock on `<data>/run.lock`: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, wrapped in a small `filelock.py` helper.
- Terminal `main.py` takes the same file lock. If the app holds it, the terminal prints «A search is already running in the app» and exits 0. The reverse gives a 409 in the app.

---

## 4. Local API

Every `/api/*` request:
- must carry `X-Hawkeye-Token: <token>`;
- may instead pass `?token=` for `GET /api/events` only, because EventSource cannot set headers;
- must have `Host` equal to `127.0.0.1:<port>` or `localhost:<port>`.

Failing either check returns 403. The token is 32 random bytes, generated per launch and never written to disk. `/` and the static files are served without the token. They contain no data.

| Method and path | Purpose |
|---|---|
| `GET /api/state` | Everything the UI needs on load: `onboarding_done`, Chrome found and version, LinkedIn profile present, current run (id, progress) or null, `job_counts`, app version, `update_available` (cached) |
| `GET /api/settings` · `PUT /api/settings` | Search settings, validated: ≥1 keyword (≤100 chars each, ≤20), ≥1 known location id, ≥1 known source. 422 with field errors otherwise |
| `GET /api/app-settings` · `PATCH /api/app-settings` | Language, theme, auto_search, onboarding_done, update_check. Validated (hours ∈ {2, 4, 6, 12, 24}) |
| `GET /api/countries?lang=ar` | `[{id, label}]` with Remote first. Labels come from Babel CLDR for the language, falling back to the API/English label |
| `GET /api/jobs?status=&source=&text=&limit=&offset=` | Store `list_jobs`, plus `total` for paging |
| `PATCH /api/jobs/{id}` | `{status}` → `set_status`. 404 for an unknown id, 422 for a bad status |
| `POST /api/runs` | Start a manual run → 202 `{run_id}`. Returns 409 `{error: "run_in_progress"}` if one is running here or in the terminal |
| `POST /api/runs/current/cancel` | 202 if a run is active, 409 otherwise |
| `GET /api/runs?limit=` · `GET /api/runs/{id}` | History, and per-site details from `run_queries` |
| `GET /api/events` | SSE stream (see §5) |
| `POST /api/linkedin/login` | Launches the Chrome binary found by `browser.find_chrome()` as a normal (not automated) process with the LinkedIn profile dir at the LinkedIn login page. Refused with 409 while a run is active, because the profile would be locked |
| `POST /api/open-data-folder` | Opens the data dir in Finder, Explorer or the file manager |
| `POST /api/report-problem` | Builds a GitHub "new issue" URL (title, body), returns it, and the UI opens it in the system browser. The body holds the version, OS, and the last 200 log lines, **redacted** (see §7) |
| `GET /api/update` | Uses the cached result if checked in the last 24 h. Otherwise calls the GitHub Releases API for the latest tag, with a 5 s timeout and failures ignored, and compares versions |
| `POST /api/reset` | Body `{confirm: "DELETE"}`. Refused while a run is active. Closes the store, deletes `hawkeye.db*`, reopens. Config is kept |

App version lives in `src/version.py` (`__version__ = "1.0.0"`).

---

## 5. Flows

**Manual search:**
1. The UI calls `POST /api/runs`.
2. `RunManager.start('manual')` acquires both locks or returns 409.
3. In a worker thread it calls `pipeline.run_search(settings, on_event=self._on_event, cancel=self._cancel, country_labels=..., store=<thread-local Store>)`.
4. `_on_event`, which runs on the worker (the run_search caller thread):
   - writes `run_queries` rows for done, failed and skipped events;
   - stamps `trigger` and the run id;
   - fans the event out to SSE subscribers through per-subscriber `queue.Queue`s, bounded at 1000. A subscriber that falls behind is dropped, and its EventSource reconnects and re-reads `/api/state`.
5. The UI updates the banner on every event. On `query_done` with `new > 0` it refetches the current jobs view.
6. After `run_search` returns:
   - `reporting.report_pending(store, notifier=notify.desktop)` sends one notification ("وُجدت 12 وظيفة جديدة" / "12 new jobs found"), then marks those jobs notified;
   - an app event `{"type":"jobs_reported","count":n}` goes out;
   - both locks are released.

**Auto-search:**
- A scheduler thread wakes every 60 s.
- When `auto_search.enabled` is on and `now ≥ last finished run + hours`, it calls `RunManager.start('auto')`.
- If a run is active, or the file lock is held, it skips and retries on the next tick.
- LinkedIn posts are included in a run only if the user enabled the source **and** `last_posts_run` is more than 24 h ago. This applies to manual runs too. `last_posts_run` is updated when a posts query actually starts.

**Cancel:** `POST /api/runs/current/cancel` sets the cancel event. In-flight queries finish, found jobs are kept and reported, and the run ends as `cancelled`.

**App start** (`app.py`):
1. Logging.
2. Legacy config migration.
3. Open the store (runs migrations).
4. `recover_interrupted_runs`.
5. Legacy seen-jobs import.
6. Start the server, then the scheduler.
7. Open the window.

Once the UI connects, `report_pending` runs once, so jobs left unreported by a killed run produce a notification.

**Window close:**
- Set cancel and show «جارٍ الإيقاف…».
- Wait up to 35 s for the worker.
- Stop the scheduler, shut down uvicorn, release the locks, exit.

---

## 6. Reporting (`reporting.py`)

```python
REPORT_LIMIT = 500
def report_pending(store, notifier) -> ReportResult   # notifier(jobs) -> bool
```

The logic is moved from `main.py` unchanged:
- fetch `limit+1`;
- report at most `REPORT_LIMIT`;
- mark only when `notifier` returns True;
- return `{reported, more_waiting}`.

**Notifiers:**
- **Terminal:**
  - email when configured;
  - otherwise "printed", which counts as reported, as today.
- **App:** `notify.desktop(jobs)`.
  - It returns True when the OS accepted the notification, **or** when notifications are unavailable or denied.
  - That second case counts as reported because the jobs are visible in the app as New. Otherwise jobs would pile up and re-notify forever.
  - The Jobs page "New" chip remains the source of truth.

---

## 7. Errors and privacy

| Situation | UI message (ar / en) | Action |
|---|---|---|
| Chrome missing | لم يُعثر على Google Chrome. سنبحث في LinkedIn وIndeed فقط. / Google Chrome was not found. Only LinkedIn and Indeed will be searched. | «تنزيل Chrome» opens google.com/chrome |
| Chrome failed or crashed | تعذّر تشغيل المتصفّح، وتخطّينا Bayt وNaukriGulf وGoogle في هذا البحث. | Retry / Report a problem |
| LinkedIn login required | منشورات LinkedIn تتطلب تسجيل الدخول. | «تسجيل الدخول إلى LinkedIn» |
| Site failed or 429 | History row: «Google Jobs: تعذّر الوصول مؤقتًا» | None. Back-off already applies |
| Offline | لا يوجد اتصال بالإنترنت. | Auto-search retries at the next interval |
| Run already active (app or terminal) | يوجد بحث قيد التنفيذ بالفعل. | Start button disabled until it ends |
| Notifications denied | Settings note: الإشعارات معطّلة في النظام. | None. Jobs stay visible as New |
| Disk or DB write failure | تعذّر حفظ البيانات. | Open data folder / Report a problem |
| Unexpected UI error | حدث خطأ غير متوقع. | Reload / Report a problem |

The pipeline's data-only events (`error` is an exception class name, `reason` a code) map to these messages in the frontend through `i18n` keys. The backend never sends display text.

**Redaction for "Report a problem"** (`web/api.py`, unit-tested):
- the home directory path and username are replaced with `~` / `<user>`;
- e-mail addresses, `token=`/`key=`/`password=` values and `GMAIL_PASS` are removed;
- URLs keep only the scheme and host.

The user sees the full issue text in the browser before submitting.

**Content safety in the UI:** job fields are scraped, untrusted text. They are rendered with Alpine `x-text` only, never `x-html`. Links open through the API in the system browser, and only `http(s)` URLs are allowed.

---

## 8. Testing

1. **Unit / integration tests** (`unittest`, `import _bootstrap`, no network, no real Chrome, temp data dir):
   - **`reporting`:** reports every pending job; marks only on success; the more-waiting flag; terminal and app notifiers.
   - **`RunManager`:**
     - single run in-process and across processes (a second process holding `run.lock`);
     - fan-out to several subscribers;
     - slow-subscriber drop;
     - cancel;
     - auto-search due, not-due, and skip-while-running;
     - the posts 24 h gate.

     Uses fake `run_search` and fetchers.
   - **API** (FastAPI `TestClient`):
     - 403 without the token or with a bad Host;
     - every endpoint's success and validation errors;
     - 409 paths;
     - reset confirmation;
     - SSE delivers events (via a fake RunManager).
   - **Schema v3:** fresh DB, v2→v3 upgrade with data, race-safe stale opener.
   - **Redaction:** home path, username, emails, tokens removed.
   - **i18n guard:** `ar.json` and `en.json` have identical key sets, and every key used in `app.js`/`index.html` exists.
   - **Countries:** Arabic labels via Babel for jo, sa, ae, plus Remote.
2. **Browser check:** run the server locally and drive the UI in the built-in browser:
   - the wizard;
   - search with progress and stop;
   - status changes;
   - ar/en with RTL/LTR;
   - dark theme;
   - a 360 px wide window.
3. **Real end-to-end:**
   - open the pywebview app with a scratch `HAWKEYE_DATA_DIR`;
   - run a real search;
   - see live progress, the jobs, and the desktop notification;
   - try a terminal run while the app is searching, and confirm it is refused.

---

## 9. Acceptance criteria

1. `python src/app.py` opens a native window on macOS. Windows and Linux are verified in sub-project 3 CI.
2. A first-time user can complete the wizard and get results without touching a terminal or a file.
3. Live progress, stop, statuses, History details and Settings actions all work as in §2.
4. Two searches can never run at once, across the app, the auto-search and the terminal.
5. Every new job leads to a desktop notification (or is visible as New when notifications are denied). None is lost when the app is killed mid-run.
6. No request leaves the machine except job searches, the daily update check, and pages the user opens.
7. All UI text exists in both فصحى and English. RTL is correct, and no colloquial strings appear.
8. The full test suite passes with pristine output. Terminal mode behaves as before, except for the "already running in the app" refusal.
