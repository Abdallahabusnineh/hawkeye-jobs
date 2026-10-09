# hawkeye-jobs — Architecture & Code Reference

Terminal job hunter. The user picks keywords, locations, and sources; it scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts, then prints and emails new listings.

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
│   ├── countries.py     # REST Countries API + countries_cache.json
│   ├── filters.py       # Title / hiring-post matching
│   ├── scraper.py       # LinkedIn + Indeed via JobSpy
│   ├── bayt.py          # Bayt.com via Selenium
│   ├── naukrigulf.py    # NaukriGulf via Selenium
│   ├── google_jobs.py   # Google Jobs via Selenium
│   ├── linkedin_posts.py # LinkedIn content/post search via Selenium (needs login)
│   ├── browser.py       # Chrome discovery, version detection, driver factory, wait_for()
│   ├── utils.py         # Shared: clean_url(), is_within_48h()
│   └── email_sender.py  # HTML email builder + Gmail SMTP sender
├── config.example.json  # Sample search settings
├── .env.example         # Credential template
├── run.sh               # Loads .env and runs the pipeline
├── requirements.txt
└── CLAUDE.md
```

Runtime files live in the per-OS data directory (see src/paths.py), never in the repo: `hawkeye.db`, `config.json`, `countries_cache.json`, `hawkeye.log`, `linkedin-profile/`.

---

## Pipeline Flow (`src/main.py` → `src/pipeline.py`)

`main.py` does: `setup_logging()` → `migrate_legacy_config()` → open `Store` → `recover_interrupted_runs()` → `import_legacy_seen_file()` → print `Data folder: ...` → `collect_settings()` → `run_search(settings, on_event=print, store=store)` → print and email `summary.new_jobs`. The store is closed in a `finally`. Ctrl+C is handled inside `run_search` (see Cancel).

`run_search(settings, on_event, cancel, *, country_labels, store, fetchers, driver_factory) -> RunSummary(run_id, state, found, new_jobs)`:

1. `catalog.plan_queries()` lists every query (`source`, `keyword`, `location_id`, `args`), sorted in `SOURCE_ORDER`. Each gets an `index`.
2. The run is recorded (`store.start_run`) and `run_started` is emitted.
3. Two lanes, one thread each, run in parallel; queries inside a lane run one after another:
   - **HTTP lane** — `linkedin`, `indeed` (JobSpy, no browser).
   - **Browser lane** — `bayt`, `naukrigulf`, `google_jobs`, `linkedin_posts` (one Chrome driver for the whole lane).
4. Lanes never touch the Store and never call `on_event`. They push messages on a queue. The thread that called `run_search` drains it: it sets each job's `keyword` and `location_id`, calls `store.add_jobs()` for that query (so a crash or Ctrl+C keeps everything saved so far), and emits events. `on_event` therefore always runs on the caller's thread. If `on_event` raises, the error is logged and ignored.
5. `store.finish_run()` records the final state, then `run_finished` is emitted. If `run_search` created its own Store, it closes it.

Each lane keeps its own `seen_urls` set (cross-source duplicates within a lane); cross-run dedup is the Store's job.

### Events

Every event is a dict with a `type` key. Values are data only (never English sentences); `main.format_event()` turns them into terminal lines.

| type | fields |
|---|---|
| `run_started` | `run_id`, `total` |
| `query_started` | `index`, `source`, `keyword`, `location_id` |
| `query_done` | `index`, `source`, `keyword`, `location_id`, `found`, `new` |
| `query_failed` | `index`, `source`, `keyword`, `location_id`, `error` (exception class name) |
| `query_skipped` | `index`, `source`, `keyword`, `location_id`, `reason` in `login_required`, `browser_unavailable` |
| `login_required` | `source` |
| `browser_failed` | `reason` in `chrome_not_found`, `start_failed`, `crashed` |
| `run_finished` | `run_id`, `state`, `found`, `new` |

Guarantees: `run_started` is first and `run_finished` last. A query's `query_started` precedes its `query_done` / `query_failed`; skipped queries have no `query_started`. Events from the two lanes may interleave. On cancel, lanes stop without emitting skip events.

### Failure handling

- One failed query emits `query_failed` and the lane continues.
- `ChromeNotFound` or a failed Chrome start: `browser_failed` and every browser query is skipped (`browser_unavailable`); the HTTP lane is unaffected.
- `LinkedInLoginRequired`: `query_failed`, then `login_required`; the remaining `linkedin_posts` queries are skipped (`login_required`).
- Chrome dying mid-run: the driver is restarted once; a second crash emits `browser_failed` (`crashed`) and skips the rest.
- Errors before `run_started` (e.g. the database cannot be written) raise. After it, `run_search` never raises: an unexpected error gives state `failed`.

### Polite throttling

Each lane waits on a `Throttle` (`src/throttle.py`) before every query: a random minimum gap between the **start** of consecutive requests to the same host (page-load time counts toward the gap). This protects users' accounts and IPs from bot detection (LinkedIn account restrictions, Google CAPTCHAs, Bayt/Cloudflare challenges, HTTP 429). Slower is acceptable; harm is not. The first request never waits; the wait sleeps in slices of at most 0.5 s and stops as soon as `cancel` is set (the lane then ends as on its pre-query cancel check, with no events for that query).

- **Host groups:** throttles are keyed by `pipeline.SITE_GROUPS` (`linkedin` and `linkedin_posts` both map to `linkedin.com`), built once in `run_search` and shared by both lanes. Slots are reserved under a lock, so two lanes never get requests closer than the gap, including on the user's own LinkedIn account. A group uses the per-field largest (min, max) among its sources, so linkedin.com gets 10-20 s.
- **Back-off:** when a query fails (not on cancel), its group's gap doubles for the rest of the run, capped at 4x (`Throttle.back_off()`).
- **Known limit:** spacing does not persist across separate `run_search` calls (a new run starts with fresh throttles); the upcoming app should keep this in mind when it allows back-to-back runs.

| Source | Gap (seconds, min-max) |
|---|---|
| linkedin | 3-6 (shares linkedin.com with linkedin_posts, so 10-20) |
| indeed | 3-6 |
| bayt | 6-10 |
| naukrigulf | 4-7 |
| google_jobs | 8-12 |
| linkedin_posts | 10-20 (the user's own LinkedIn account) |

Values live in `pipeline.POLITE_DELAYS`. `run_search(..., delays={...})` overrides them (tests pass `{}`); a source missing from `delays` is not throttled.

### Cancel

Pass a `threading.Event` as `cancel`, or press Ctrl+C: a `KeyboardInterrupt` on the calling thread sets the event instead of aborting. Each lane checks it before its next query, so cancel takes at most one in-flight query. Lanes are always joined (the Chrome driver is quit), the run is recorded as `cancelled`, and `run_finished` is still emitted. Jobs saved so far are kept, and `main.py` prints and emails them.

---

## Source Files

### `src/settings.py` — Terminal prompts + saved config

`collect_settings()` loads `config.json` if present and shows a Yes/No picker (`↑↓` + enter). Yes reuses it. No (or no saved file) prompts for keywords (typed), then a searchable country list (`type` to filter, `↑↓` move, space select, `*` all visible, enter confirm), then **Confirm these countries: …?** (`Yes, continue` / `No, choose again`). Same confirm step after website checkboxes (`↑↓` move, space select, `a` all, `d` remove all, enter confirm). Writes `config.json`. If stdin is not a TTY, it falls back to numbered lists and `[Y/n]` confirms.

Country names are fetched live from a public country API every `bash run.sh` run (`src/countries.py` tries FIRST.org, then a GitHub countries dump, then CountriesNow). If every request fails, `countries_cache.json` from a previous successful fetch is used. If the data-dir cache does not exist yet (first run after upgrading), the legacy repo-root `countries_cache.json` is read as a fallback (read-only: never copied or written; `countries.LEGACY_CACHE`). There is no hardcoded country picker list.

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

`plan_queries(keywords, location_ids, source_ids, country_labels=None)` is what the pipeline uses: a flat list of `{source, keyword, location_id, args}` (`location_id` is `None` for LinkedIn posts), where `args` is exactly what that source's `fetch_*` takes. `expand_searches(...)` groups the same `args` per source:

```python
{
  "linkedin": [{"term", "location"}, ...],
  "indeed": [{"term", "location", "country"}, ...],
  "bayt": [(term_slug, country_slug), ...],
  "naukrigulf": [(keyword, location_slug), ...],
  "google_jobs": [(term, location), ...],
  "linkedin_posts": ["hiring {keyword}", "looking for {keyword}", ...],
}
```

### `src/filters.py` — Keyword matching

`keyword_stems()` drops generic role words (`developer`, `engineer`, …). `is_relevant_job(title, keywords)` keeps a title if a stem matches as a whole word (`mobile` will not match `automobile`). `is_hiring_post(text, keywords)` additionally requires a hiring signal.

### `src/scraper.py` — LinkedIn + Indeed

Uses the `python-jobspy` library. No browser required. Queries come from `catalog.plan_queries()`, not hardcoded.

**`_parse_jobs(jobs, seen_urls, location, keywords) -> list`**

- `clean_url()` before dedup
- `is_relevant_job()` for title filtering
- `is_within_48h()` for date filtering

**`fetch_linkedin(args, seen_urls, keywords)` / `fetch_indeed(...)`**

Each runs ONE JobSpy search (`hours_old=48`) and raises on error. `pipeline.py` loops over the queries, so one failed search does not abort the run.

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

### `src/bayt.py` — Bayt.com Scraper

Covers: Jordan, UAE, Saudi Arabia, Kuwait, Qatar, Oman, Bahrain.

**URL pattern:**
```
https://www.bayt.com/en/{country_slug}/jobs/{term_slug}-jobs/
```
Example: `https://www.bayt.com/en/uae/jobs/flutter-developer-jobs/`

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

**`_parse_bayt_page(html, seen_urls, location, keywords)`** — parses raw HTML with BeautifulSoup. Applies `is_relevant_job()` and `is_within_48h()`.

---

### `src/naukrigulf.py` — NaukriGulf Scraper

Covers: Gulf-wide, UAE, Saudi Arabia, Kuwait, Qatar, Bahrain.

**URL pattern:**
```python
def _build_url(keyword, location_slug):
    kw = keyword.replace(" ", "-")
    if location_slug not in ("gulf-region", ""):
        return f"https://www.naukrigulf.com/{kw}-jobs-in-{location_slug}"
    return f"https://www.naukrigulf.com/{kw}-jobs"
```
Example: `https://www.naukrigulf.com/flutter-developer-jobs-in-uae`

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

Each `link_grps[i]` contains multiple `<a>` tags (one per platform hosting the job). The scraper takes the first non-Google URL from each group.

**Google domains skipped:**
```python
SKIP_DOMAINS = ["google.com", "google.jo", "google.ae", "google.co", "accounts.google", "goo.gl"]
```

**Note:** Google Jobs doesn't expose a `date_posted` field in its HTML, so `published` is set to `""`. Jobs are included regardless of age (no 48h filter here — Google Jobs already surfaces recent jobs).

**Wait:** `wait_for(driver, CARD_SELECTOR, 15)` (`.PUpOsf`) for the jobs panel to render. A timeout logs a warning.

**Entry point:** `fetch_google_jobs(driver, args, seen_urls, keywords)` runs ONE query (`args = (term, location)`) and raises on error.

---

### `src/linkedin_posts.py` — LinkedIn Content/Post Scraper

Scrapes LinkedIn's **global content search** (feed posts) — *not* the Jobs board that `scraper.py`/JobSpy reads. Targets hiring posts like "We're hiring a Flutter developer!".

**Authentication (required):** LinkedIn shows an auth wall to anonymous visitors. The scraper reuses a **persistent Chrome profile** created by `create_driver(user_data_dir=...)`. The pipeline passes `paths.linkedin_profile_dir()`, resolved in this order: `LINKEDIN_PROFILE_DIR` if set; else the old `~/.hawkeye-linkedin-profile` if it exists (so existing users stay logged in); else `<data dir>/linkedin-profile`. Log into LinkedIn **once by hand** in that profile; the session cookie is reused every run. If logged out, `fetch_linkedin_posts()` raises `LinkedInLoginRequired` (checked before and after the wait); the pipeline emits `login_required`, skips the remaining post queries, and carries on with the other sources.

**First-time setup:**
```bash
# Open Chrome with the same profile the automation uses, sign into LinkedIn, then close it.
open -a "Google Chrome" --args --user-data-dir="<paths.linkedin_profile_dir()>"
```

**URL pattern:**
```
https://www.linkedin.com/search/results/content/?keywords=<enc>&datePosted=%22past-24h%22&sortBy=%22date_posted%22
```
`datePosted=past-24h` keeps posts fresh; `sortBy=date_posted` surfaces newest first. Cross-run dedup (`store.py`) prevents re-emailing the same post.

**Search phrases:** built from the user's keywords, e.g. `hiring Flutter Developer`, `looking for Flutter Developer`.

**Relevance filter (`is_hiring_post`):** does **not** use `is_relevant_job()` (post text is prose, not a title). A post is kept only if its text mentions a user keyword stem **and** contains a hiring signal (`hiring`, `looking for`, `wanted`, `vacancy`, `open position`, …). Recency is enforced by the URL filter plus `_post_within_48h()` for LinkedIn's compact times (`5h`, `1d`, `2w`).

**HTML selectors (as of 2026 — LinkedIn changes its DOM often):**
| Data | Selector |
|------|----------|
| Post card | `div[data-urn*="urn:li:activity"]` (fallback `.feed-shared-update-v2`) |
| Post URL | built from `data-urn` → `/feed/update/<urn>/` |
| Post text | `.update-components-text, .feed-shared-update-v2__description, .feed-shared-inline-show-more-text` |
| Author | `.update-components-actor__title, .update-components-actor__name` |
| Relative time | `.update-components-actor__sub-description` |

**Wait / scroll:** `wait_for(driver, CARD_SELECTOR, 15)`, then 4 scrolls × 2s to lazy-load more posts. If no cards load and nothing is parsed it logs a timeout warning (page title included).

**Note:** `source` is `"LinkedIn Post"`; `title` is the post's opening hook (truncated to ~90 chars); `description` is `"{author} · LinkedIn hiring post"`.

---

### `src/utils.py` — Shared Utilities

**`clean_url(url: str) -> str`**

Strips UTM and tracking query parameters so the same job from different sources maps to the same canonical URL for deduplication.

Strips: `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, `source`, `ref`, `referer`, `tracking`

Example: `https://indeed.com/viewjob?jk=abc123&utm_source=google` → `https://indeed.com/viewjob?jk=abc123`

**`is_within_48h(date_str: str) -> bool`**

Returns `True` if the job was posted within 48 hours. `MAX_AGE_HOURS = 48`.

Handles multiple formats:
| Input format | Example | Behavior |
|---|---|---|
| Empty / None / NaN | `""`, `"none"`, `"nan"` | `True` (benefit of the doubt) |
| Immediate | `"just now"`, `"today"`, `"moments ago"` | `True` |
| Hours ago | `"3 hours ago"` | `True` if ≤ 48 |
| Days ago | `"1 day ago"`, `"2 days ago"` | `True` if ≤ 2 |
| Yesterday | `"yesterday"` | `True` |
| Old markers | `"30+ days ago"`, `"3 weeks ago"`, `"1 month ago"` | `False` |
| Day-month | `"29 Apr"`, `"1 May"` | Compare against now, `True` if ≤ 48h |
| ISO date | `"2026-05-04"` | Compare against now, `True` if ≤ 48h |
| Unparseable | anything else | `True` |

---

### `src/store.py` — SQLite store (jobs, statuses, runs)

Replaces the old `tracker.py` / `seen_jobs.json`. Keeps every job found, so history and statuses can be shown. The database is `paths.db_path()` (`hawkeye.db`).

**Tables** (`PRAGMA user_version` = `SCHEMA_VERSION`, created on first open):
- `runs` — one row per search: `started_at`, `finished_at`, `state`, `settings_json`, `found_count`, `new_count`. States: `running`, `done`, `cancelled`, `failed`.
- `jobs` — one row per unique URL. `id` = `job_id(url)` = MD5 of `clean_url(url).strip().rstrip("/")` (same as the old tracker). Columns: `title`, `url`, `description`, `source`, `location`, `location_id`, `keyword`, `published`, `status`, `status_changed_at`, `first_seen_at`, `last_seen_at`, `first_run_id`.
- `legacy_seen` — hashes imported from the old `seen_jobs.json`; used for dedup only.

**Statuses:** `new`, `saved`, `applied`, `dismissed` (`set_status`; `list_jobs` hides dismissed unless asked).

**`add_jobs(run_id, jobs) -> list`** is called once per query. A job whose id is in `legacy_seen` is skipped. A job already in `jobs` only gets `last_seen_at` updated. Otherwise it is inserted with status `new`. It returns only the genuinely new jobs (each with an `id`), which are what the terminal prints and emails.

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

**`send_email(jobs: list)`**

Sends both `text/plain` (fallback) and `text/html` parts. Raises the SMTP exception if sending fails (so the error appears in the log).

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
    "location_id": str,   # Normalised country id of the query (e.g. "jo"); None for LinkedIn posts
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
| LinkedIn Posts | Global (content search — not location-scoped) |

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

```
platformdirs             # Per-OS data directory
python-jobspy==1.1.80    # LinkedIn + Indeed scraper
pandas                   # Required by jobspy
requests                 # HTTP utilities
beautifulsoup4           # HTML parsing for Bayt, NaukriGulf, Google Jobs
undetected-chromedriver  # Selenium wrapper that bypasses bot detection
setuptools               # Required on Python 3.12+ (distutils removed)
```

Install: `pip install -r requirements.txt`

---

## Running Locally

```bash
cp .env.example .env   # fill in GMAIL_USER / GMAIL_PASS / RECIPIENT_EMAIL
bash run.sh            # prompts for keywords, locations, sources
```

To change saved searches: pick **No** on the reuse menu, or delete `config.json` from the data directory. Tests: `venv/bin/python3 -m unittest discover -s tests -v`.
