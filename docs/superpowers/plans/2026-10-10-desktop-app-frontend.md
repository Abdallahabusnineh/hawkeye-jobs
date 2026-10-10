# Desktop App Front-end Implementation Plan (Plan 2b of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder `src/web/static/index.html` with the real Hawkeye Jobs window: Jobs (with live progress), Search, History, Settings and the first-run wizard, in formal Arabic and English, on top of the Plan 2a backend.

**Architecture:**
- Static files only, no build step. `index.html` + `styles.css` + three ES modules:
  - `logic.js`: pure helpers (no DOM, no network), unit-tested with `node --test`;
  - `api.js`: `fetch` wrapper that adds `X-Hawkeye-Token`, and the token handling;
  - `app.js`: one Alpine.js component (`hawkeye`) that owns all page state.
- Alpine.js 3.14.9 is vendored under `static/vendor/` with its licence and a SHA-256 checksum.
- Text lives in `static/i18n/ar.json` and `static/i18n/en.json`. The backend sends codes only.
- Live progress comes from `GET /api/events` (SSE). On every (re)connect the UI re-reads `GET /api/state`.
- Small backend additions:
  - `POST /api/estimate` (search-duration estimate);
  - security headers (CSP, `Referrer-Policy: no-referrer`);
  - safer reset order;
  - `--port` for development;
  - a launch-wiring test.

**Tech stack:**
- Alpine.js 3.14.9, plain CSS (logical properties for RTL), ES modules.
- Python `unittest` for static checks.
- Node 18+ `node:test` for the JS helpers. The Python suite runs them when `node` is on PATH and skips otherwise.

**Spec:** `docs/superpowers/specs/2026-10-10-desktop-app-design.md` (§2 UX, §4 API, §7 errors, §8 testing, §9 acceptance). Approved mockups are in `docs/superpowers/specs/mockups/2026-10-10-app-ui/`. The API as built is the table in `docs/ARCHITECTURE.md` → "Desktop app".

## Global Constraints

- Use `venv/bin/python3` for everything. Run tests with `venv/bin/python3 -m unittest discover -s tests -v`. 386 tests pass at the start.
- Every new Python test file starts with:
  ```python
  sys.path.insert(0, str(Path(__file__).resolve().parent))
  import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path
  ```
- Tests never use the network, Chrome, a real window or a real notification.
- Never run `src/main.py` or `run.sh` for real, and never touch the repo-root `config.json`, `seen_jobs.json`, `countries_cache.json` or `.env`.
- **No build step, no CDN at runtime:** every script and style is served from `src/web/static/`. Alpine.js is exactly **3.14.9**, vendored.
- **Job fields are untrusted scraped text.** Render them only with `x-text` or attribute bindings. Never use `x-html`, `innerHTML`, `outerHTML`, `insertAdjacentHTML` or `document.write`. Links open only through `POST /api/open-url`.
- **All UI text comes from `i18n/ar.json` / `i18n/en.json`.** Two exceptions:
  - the product name "Hawkeye Jobs" (the same in both languages);
  - the language names «العربية» / "English" in the language pickers.
- Arabic is formal فصحى, with no colloquial words.
- Both dictionaries have identical key sets and identical `{placeholders}` per key.
- The backend never sends display text. The UI maps codes to i18n keys.
- **RTL:**
  - `<html dir>` is `rtl` for Arabic and `ltr` for English;
  - CSS uses logical properties (`margin-inline-start`, `inset-inline`, `border-inline-end` …), never `left`/`right` for layout;
  - numbers use Latin digits in both languages (`ar-u-nu-latn` for dates).
- **The token:**
  - `?token=` is removed from the address bar on load (`history.replaceState`) and kept in `sessionStorage`;
  - it is never logged or put in any other URL except `GET /api/events?token=`.
- UI language codes are exactly `"ar"` / `"en"`. Themes are `"auto"`, `"light"`, `"dark"`. Auto-search hours are one of `2, 4, 6, 12, 24`.
- It must work at a 360 px wide window, with no horizontal page scroll.
- Commits end with a blank line and a `Co-Authored-By:` trailer naming the model you run as.
- Follow the existing Python style (`name.py — summary` + `====` docstrings).

## Review Focus

1. **A scraped job title or company contains HTML or script** (for example `<img src=x onerror=alert(1)>`). It must appear as literal text, with nothing executed. Tests:
   - Task 2 `test_untrusted_text_never_rendered_as_html`;
   - Task 6 browser check step "hostile job text".
2. **The user reloads the window (Cmd/Ctrl+R) after the token was stripped from the URL.** The UI must keep working, not show 403s. Tests:
   - Task 3 `takeToken reads session storage after a reload`;
   - Task 6 browser check step "reload".
3. **Arabic plurals and RTL:** "قبل 5 دقائق", "قبل 11 دقيقة", "قبل دقيقتين"; layout mirrored. Tests:
   - Task 3 `pluralKey picks the Arabic form`;
   - Task 2 `test_dictionaries_have_identical_keys`;
   - Task 6 browser check (ar + en screenshots).
4. **The SSE connection drops mid-run** (slow-subscriber drop, sleep/wake). The banner must resync from `/api/state` and not freeze or double-count. Tests:
   - Task 3 `fromSnapshot keeps the current query of the same run`;
   - Task 4 `connectEvents` re-reads state in `onopen`;
   - Task 6 browser check step "reconnect".
5. **Errors from the API** (409 `run_in_progress` from a terminal run, 422 field errors, server gone). Each must show a فصحى/English message with one clear action, never raw codes or English-only text. Tests:
   - Task 3 `apiErrorKey`;
   - Task 2 `test_dynamic_key_families_exist`.

---

### Task 1: Backend support for the UI (estimate, security headers, reset order, `--port`, launch wiring test)

**Files:**
- Modify: `src/pipeline.py` (add `estimate_seconds`), `src/web/api.py` (add `POST /api/estimate`; reset deletion order), `src/web/server.py` (security headers), `src/app.py` (`--port`)
- Test: `tests/test_pipeline.py`, `tests/test_api.py`, `tests/test_app.py`

**Interfaces:**
- Produces (used by Tasks 4 and 6):
  - `pipeline.estimate_seconds(queries: list) -> int`
  - `POST /api/estimate {keywords, locations, sources}` → 200 `{"queries": n, "seconds": s}`. Missing or bad input gives `{"queries": 0, "seconds": 0}`, never 422.
  - Every response carries `Content-Security-Policy`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.
  - `app.py --no-window --port 8765` serves on a fixed port.
  - Reset deletes `hawkeye.db-wal`, then `-shm`, then `hawkeye.db`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py` (inside the module, as a new class, before the `if __name__ == "__main__"` block if there is one):

```python
class EstimateTests(unittest.TestCase):
    def test_estimate_takes_the_slower_lane(self):
        from pipeline import estimate_seconds
        queries = [{"source": "linkedin"}, {"source": "linkedin"}, {"source": "google_jobs"}]
        # http lane: 2 × ((3+6)/2 + 4) = 17; browser lane: (8+12)/2 + 8 = 18
        self.assertEqual(estimate_seconds(queries), 18)

    def test_estimate_of_nothing_is_zero(self):
        from pipeline import estimate_seconds
        self.assertEqual(estimate_seconds([]), 0)
```

Append to `tests/test_api.py` (new classes before the final `if __name__ == "__main__"` block):

```python
class EstimateApiTests(ApiTestCase):
    def post(self, path, body):
        return self.client.post(path, json=body, headers={"X-Hawkeye-Token": TOKEN})

    def test_estimate_counts_queries_and_seconds(self):
        response = self.post("/api/estimate", {"keywords": ["Java"], "locations": ["jo"],
                                               "sources": ["linkedin", "bayt"]})
        self.assertEqual(response.status_code, 200)
        # linkedin (http): (3+6)/2 + 4 = 8.5; bayt (browser): (6+10)/2 + 8 = 16
        self.assertEqual(response.json(), {"queries": 2, "seconds": 16})

    def test_estimate_with_missing_or_bad_input_is_zero(self):
        for body in ({}, {"keywords": [], "locations": ["jo"], "sources": ["linkedin"]},
                     {"keywords": "Java", "locations": ["jo"], "sources": ["linkedin"]},
                     {"keywords": ["Java"], "locations": ["jo"], "sources": ["nope"]}):
            self.assertEqual(self.post("/api/estimate", body).json(), {"queries": 0, "seconds": 0}, body)


class SecurityHeaderTests(ApiTestCase):
    def assert_secure(self, response):
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_headers_on_static_api_and_refusals(self):
        self.assert_secure(self.client.get("/"))
        self.assert_secure(self.get("/api/state"))
        self.assert_secure(self.client.get("/api/state"))  # 403 without the token


class ResetOrderTests(ApiTestCase):
    def test_reset_deletes_wal_and_shm_before_the_database(self):
        from pathlib import Path
        from unittest.mock import patch
        for suffix in ("-wal", "-shm"):
            Path(str(self.db) + suffix).touch()
        deleted = []
        real_unlink = Path.unlink

        def recording_unlink(path, *args, **kwargs):
            deleted.append(path.name)
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", recording_unlink):
            response = self.client.post("/api/reset", json={"confirm": "DELETE"},
                                        headers={"X-Hawkeye-Token": TOKEN})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(deleted, ["t.db-wal", "t.db-shm", "t.db"])
```

Append to `tests/test_app.py` inside `class AppTests` (before the final `if __name__` block):

```python
    def test_port_option_reaches_build(self):
        self.assertEqual(app._parse(["--no-window", "--port", "8765"]).port, 8765)
        self.assertIsNone(app._parse([]).port)

    def test_main_recovers_then_starts_auto_then_reports(self):
        from unittest.mock import patch
        parent = MagicMock()
        parts = MagicMock()
        parts.manager = parent.manager
        parts.auto = parent.auto
        thread = MagicMock()
        thread.is_alive.return_value = False
        with patch.object(app, "setup_logging"), patch.object(app, "_prepare_data"), \
                patch.object(app, "build", return_value=parts) as build, \
                patch.object(app, "_serve", return_value=thread), patch.object(app, "shutdown"), \
                self.assertLogs("app", "ERROR"), patch("builtins.print"), patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                app.main(["--no-window", "--port", "8765"])
        build.assert_called_once_with(port=8765)
        names = [c[0] for c in parent.mock_calls]
        self.assertEqual(names[:3], ["manager.recover_interrupted", "auto.start", "manager.report_pending_now"])

    def test_prepare_data_never_recovers_runs_without_the_lock(self):
        from unittest.mock import patch
        with patch.object(app, "migrate_legacy_config"), patch.object(app, "Store") as store_cls:
            app._prepare_data()
        store = store_cls.return_value.__enter__.return_value
        store.recover_interrupted_runs.assert_not_called()
        store.import_legacy_seen_file.assert_called_once()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `venv/bin/python3 -m unittest tests.test_pipeline tests.test_api tests.test_app -v 2>&1 | tail -20`

Expected failures:
- `ImportError: cannot import name 'estimate_seconds'`;
- 404/`{"error": "not_found"}` for `/api/estimate`;
- `KeyError: 'content-security-policy'`;
- the reset order is `['t.db', 't.db-wal', 't.db-shm']`;
- `AttributeError: 'Namespace' object has no attribute 'port'`.

`test_prepare_data_never_recovers_runs_without_the_lock` already passes. It pins existing behaviour.

- [ ] **Step 3: Implement**

`src/pipeline.py`:
- add `BROWSER_SOURCES` to the existing `from catalog import ...` line (add the import if `catalog` is imported differently, e.g. `from catalog import BROWSER_SOURCES, plan_queries`);
- after the `SITE_GROUPS` dict, add:

```python
# Rough time per query on top of the polite gap (page load, parsing). Only used
# for the estimate shown on the Search page; the real pacing is the Throttles.
_WORK_SECONDS = {"http": 4, "browser": 8}


def estimate_seconds(queries: list) -> int:
    """Approximate run time for plan_queries() output. The two lanes run in parallel."""
    lanes = {"http": 0.0, "browser": 0.0}
    for query in queries:
        lane = "browser" if query["source"] in BROWSER_SOURCES else "http"
        low, high = POLITE_DELAYS.get(query["source"], (0, 0))
        lanes[lane] += (low + high) / 2 + _WORK_SECONDS[lane]
    return int(round(max(lanes.values())))
```

`src/web/api.py`:
- add these imports next to the existing ones:

```python
from catalog import SOURCES, plan_queries
from pipeline import estimate_seconds
```

- add the endpoint after `put_settings`:

```python
def _str_list(value, limit):
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value[:limit] if isinstance(item, str) and item.strip()]


@router.post("/estimate")
def estimate(request: Request, body: dict):
    """How long a search with these (unsaved) settings would take, roughly. Never fails."""
    keywords = _str_list(body.get("keywords"), 20)
    locations = _str_list(body.get("locations"), 300)
    sources = [s for s in _str_list(body.get("sources"), 20) if s in SOURCES]
    if not (keywords and locations and sources):
        return {"queries": 0, "seconds": 0}
    try:
        queries = plan_queries(keywords, locations, sources)
    except Exception:
        log.warning("Estimate failed for %r", body, exc_info=True)
        return {"queries": 0, "seconds": 0}
    return {"queries": len(queries), "seconds": estimate_seconds(queries)}
```

- in `reset`, change the deletion order so a partial failure never leaves an old WAL next to a new database:

```python
            for suffix in ("-wal", "-shm", ""):  # WAL/SHM first: never leave an old WAL beside a new DB
```

`src/web/server.py`: add the constant under `EVENTS_PATH`:

```python
# 'unsafe-eval' is required by the standard Alpine.js build (it compiles x-* expressions).
# Everything else is locked to this origin; the UI loads nothing from the internet.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}
```

Then replace the body of the `guard` middleware so every response, including the 403s, gets the headers:

```python
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            response = JSONResponse({"error": "forbidden"}, status_code=403)
        elif request.url.path.startswith("/api/") and not _token_ok(request, token):
            response = JSONResponse({"error": "forbidden"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response
```

and add this module-level helper above `create_app`:

```python
def _token_ok(request: Request, token: str) -> bool:
    supplied = request.headers.get("x-hawkeye-token")
    if supplied is None and request.url.path == EVENTS_PATH:
        supplied = request.query_params.get("token")
    return bool(supplied) and hmac.compare_digest(supplied.encode(), token.encode())
```

`src/app.py`:
- in `_parse`, add `parser.add_argument("--port", type=int, default=None, help="fixed port (development)")`;
- in `main`, change `parts = build()` to `parts = build(port=args.port)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python3 -m unittest tests.test_pipeline tests.test_api tests.test_app -v 2>&1 | tail -5`. Expected: `OK`.

- [ ] **Step 5: Full suite and commit**

Run: `venv/bin/python3 -m unittest discover -s tests 2>&1 | tail -3`. Expected: `OK`.

```bash
git add src/pipeline.py src/web/api.py src/web/server.py src/app.py tests/test_pipeline.py tests/test_api.py tests/test_app.py
git commit -m "Backend for the UI: estimate endpoint, security headers, safer reset order, --port"
```

---

### Task 2: Vendored Alpine.js, the i18n dictionaries, and the static-file guards

**Files:**
- Create:
  - `src/web/static/vendor/alpine.min.js`, `src/web/static/vendor/LICENSE.md`, `src/web/static/vendor/SHA256SUMS`
  - `src/web/static/i18n/en.json`, `src/web/static/i18n/ar.json`
  - `tests/test_frontend.py`

**Interfaces:**
- Produces (used by Tasks 3–6):
  - the i18n key set below. Every key the UI uses must be in both files;
  - `tests/test_frontend.py`, which Tasks 3 and 4 extend. It scans `index.html`, `app.js`, `logic.js` and `api.js`. Files that do not exist yet are skipped by the scans.

- [ ] **Step 1: Vendor Alpine.js 3.14.9**

```bash
mkdir -p src/web/static/vendor src/web/static/i18n
curl -fsSL https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js -o src/web/static/vendor/alpine.min.js
curl -fsSL https://raw.githubusercontent.com/alpinejs/alpine/v3.14.9/LICENSE.md -o src/web/static/vendor/LICENSE.md
(cd src/web/static/vendor && shasum -a 256 alpine.min.js > SHA256SUMS)
head -c 120 src/web/static/vendor/alpine.min.js; echo; cat src/web/static/vendor/SHA256SUMS; head -3 src/web/static/vendor/LICENSE.md
```

Expected:
- the JS starts with minified code (`(()=>{` or similar);
- `SHA256SUMS` holds one line, `<64 hex>  alpine.min.js`;
- the licence starts with `# MIT License`.

If either download fails, stop and report BLOCKED. Do not substitute another version.

- [ ] **Step 2: Write the failing guard tests**

Create `tests/test_frontend.py`:

```python
"""
test_frontend.py — Static checks for the window's front-end files
==================================================================
No browser needed. Checks the i18n dictionaries, the keys the code uses, that
scraped text can never be rendered as HTML, that nothing loads from the
internet, the vendored Alpine.js checksum, and (when node is installed) runs the
JS unit tests in tests/js/.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from catalog import SOURCE_ORDER
from settings import AUTO_HOURS, THEMES

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "web" / "static"
CODE_FILES = ("index.html", "app.js", "logic.js", "api.js")
# ("app.*" is deliberately not scanned: it would match the file name "app.js".)
NAMESPACES = ("nav", "run", "banner", "jobs", "age", "source", "search", "hours", "estimate", "errors",
              "linkedin", "notice", "error", "action", "history", "settings", "theme", "wizard")
# Keys appear as 'single-quoted' strings in index.html (inside t('...') calls) and as "double-quoted"
# strings in the JS modules. Double-quoted HTML attributes are Alpine expressions, not keys.
KEY_PATTERN = r"((?:%s)\.[a-z0-9_.]*[a-z0-9_])" % "|".join(NAMESPACES)
PLACEHOLDER = re.compile(r"\{(\w+)\}")
PLURAL_FORMS = ("one", "two", "few", "many", "other")
PLURAL_FAMILIES = ("age.minutes", "age.hours", "age.days", "estimate.minutes")
# Levantine/Egyptian/Gulf words that must never appear in the formal Arabic UI.
COLLOQUIAL = ("هلأ", "هلق", "هلّق", "بدك", "بدّك", "شو", "مش", "هيك", "منيح", "كتير", "عشان", "ليش", "لسا",
              "لسّا", "بس", "هاد", "هاي", "تبعك", "اشي", "إشي", "يلا", "مو", "ايش", "إيش", "زي", "خلص")
HTML_SINKS = ("x-html", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write")


def _load(lang):
    return json.loads((STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def _code(name):
    path = STATIC / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


class DictionaryTests(unittest.TestCase):
    def setUp(self):
        self.ar, self.en = _load("ar"), _load("en")

    def test_dictionaries_have_identical_keys(self):
        self.assertEqual(sorted(set(self.ar) - set(self.en)), [])
        self.assertEqual(sorted(set(self.en) - set(self.ar)), [])

    def test_every_value_is_non_empty_text(self):
        for lang, table in (("ar", self.ar), ("en", self.en)):
            for key, value in table.items():
                self.assertIsInstance(value, str, f"{lang}:{key}")
                self.assertTrue(value.strip(), f"{lang}:{key} is empty")

    def test_placeholders_match_between_languages(self):
        for key in self.en:
            self.assertEqual(set(PLACEHOLDER.findall(self.ar[key])), set(PLACEHOLDER.findall(self.en[key])), key)

    def test_arabic_is_formal(self):
        for key, value in self.ar.items():
            for word in COLLOQUIAL:
                pattern = r"(?<![؀-ۿ])" + re.escape(word) + r"(?![؀-ۿ])"
                self.assertIsNone(re.search(pattern, value), f"colloquial «{word}» in ar:{key}")

    def test_dynamic_key_families_exist(self):
        families = []
        families += [f"source.{s}{suffix}" for s in SOURCE_ORDER for suffix in ("", ".hint", ".short")]
        families += [f"jobs.filter.{f}" for f in ("new", "saved", "applied", "all")]
        families += [f"jobs.empty.{f}" for f in ("new", "saved", "applied", "all", "text")]
        families += [f"hours.{h}" for h in AUTO_HOURS]
        families += [f"theme.{t}" for t in THEMES]
        families += [f"history.trigger.{t}" for t in ("manual", "auto", "terminal")]
        families += [f"history.state.{s}" for s in ("running", "done", "cancelled", "failed")]
        families += [f"history.q.skipped.{r}" for r in ("login_required", "browser_unavailable", "other")]
        families += [f"notice.browser.{r}" for r in ("chrome_not_found", "start_failed", "crashed")]
        families += [f"errors.keywords.{c}" for c in ("invalid", "required", "too_long", "too_many")]
        families += [f"errors.locations.{c}" for c in ("invalid", "required")]
        families += [f"errors.sources.{c}" for c in ("invalid", "required")]
        families += [f"error.{c}" for c in ("unexpected", "no_settings", "run_in_progress", "not_running",
                                            "chrome_not_found", "outside_data_dir", "files_in_use", "not_found",
                                            "forbidden", "offline_app", "report_failed")]
        families += [f"{base}.{form}" for base in PLURAL_FAMILIES for form in PLURAL_FORMS]
        missing = [key for key in families if key not in self.en]
        self.assertEqual(missing, [])

    def test_literal_keys_used_in_code_exist(self):
        used = set()
        for name in CODE_FILES:
            quote = "'" if name.endswith(".html") else '"'
            used.update(re.findall(quote + KEY_PATTERN + quote, _code(name)))
        # Plural bases ("age.minutes") are completed at runtime with .one/.two/.few/.many/.other.
        missing = sorted(key for key in used if key not in self.en and f"{key}.other" not in self.en)
        self.assertEqual(missing, [])


class SafetyTests(unittest.TestCase):
    def test_untrusted_text_never_rendered_as_html(self):
        for name in CODE_FILES:
            text = _code(name)
            for sink in HTML_SINKS:
                self.assertNotIn(sink, text, f"{sink} in {name}")

    def test_only_local_scripts_and_styles(self):
        html = _code("index.html")
        for match in re.finditer(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", html):
            target = match.group(1)
            self.assertFalse(re.match(r"^(?:[a-z]+:)?//", target, re.I), f"remote resource {target}")

    def test_vendored_alpine_matches_checksum(self):
        vendor = STATIC / "vendor"
        expected, name = (vendor / "SHA256SUMS").read_text().split()
        self.assertEqual(name, "alpine.min.js")
        self.assertEqual(hashlib.sha256((vendor / name).read_bytes()).hexdigest(), expected)
        self.assertIn("MIT License", (vendor / "LICENSE.md").read_text(encoding="utf-8"))


class JsUnitTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_js_unit_tests_pass(self):
        files = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
        if not files:
            self.skipTest("no JS tests yet")
        result = subprocess.run(["node", "--test", *files], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `venv/bin/python3 -m unittest tests.test_frontend -v 2>&1 | tail -15`. Expected: errors with `FileNotFoundError: .../i18n/ar.json`.

- [ ] **Step 4: Write the dictionaries**

Create `src/web/static/i18n/en.json`:

```json
{
  "app.loading": "Loading…",
  "nav.label": "Main navigation",
  "nav.jobs": "Jobs",
  "nav.search": "Search",
  "nav.history": "History",
  "nav.settings": "Settings",
  "run.start": "Start search",
  "run.stop": "Stop search",
  "run.stopping": "Stopping…",
  "banner.starting": "Preparing the search…",
  "banner.progress": "Searching… {done} of {total}",
  "banner.new": "New jobs so far: {n}",
  "banner.now": "Now: {source} · “{keyword}” in {location}",
  "banner.now_nolocation": "Now: {source} · “{keyword}”",
  "jobs.filter.new": "New",
  "jobs.filter.saved": "Saved",
  "jobs.filter.applied": "Applied",
  "jobs.filter.all": "All",
  "jobs.search_placeholder": "Search titles and companies…",
  "jobs.list_label": "Jobs",
  "jobs.open": "Open posting",
  "jobs.save": "Save",
  "jobs.applied": "Applied",
  "jobs.dismiss": "Not interested",
  "jobs.load_more": "Show more",
  "jobs.empty.new": "No new jobs. Start a search to find more.",
  "jobs.empty.saved": "You have not saved any jobs yet.",
  "jobs.empty.applied": "You have not marked any job as applied yet.",
  "jobs.empty.all": "No jobs yet. Set up your search, then start it.",
  "jobs.empty.text": "No jobs match your search.",
  "jobs.dismissed_badge": "Not interested",
  "jobs.dismissed_toast": "The job was hidden.",
  "jobs.found_age": "Found {age}",
  "age.now": "just now",
  "age.minutes.one": "1 minute ago",
  "age.minutes.two": "2 minutes ago",
  "age.minutes.few": "{n} minutes ago",
  "age.minutes.many": "{n} minutes ago",
  "age.minutes.other": "{n} minutes ago",
  "age.hours.one": "1 hour ago",
  "age.hours.two": "2 hours ago",
  "age.hours.few": "{n} hours ago",
  "age.hours.many": "{n} hours ago",
  "age.hours.other": "{n} hours ago",
  "age.days.one": "1 day ago",
  "age.days.two": "2 days ago",
  "age.days.few": "{n} days ago",
  "age.days.many": "{n} days ago",
  "age.days.other": "{n} days ago",
  "source.linkedin": "LinkedIn — Jobs",
  "source.linkedin.hint": "All countries",
  "source.linkedin.short": "LinkedIn",
  "source.indeed": "Indeed",
  "source.indeed.hint": "Not available for Jordan or Remote",
  "source.indeed.short": "Indeed",
  "source.bayt": "Bayt",
  "source.bayt.hint": "Jordan and the Gulf only",
  "source.bayt.short": "Bayt",
  "source.naukrigulf": "NaukriGulf",
  "source.naukrigulf.hint": "Gulf countries only",
  "source.naukrigulf.short": "NaukriGulf",
  "source.google_jobs": "Google Jobs",
  "source.google_jobs.hint": "Collects jobs from many sites",
  "source.google_jobs.short": "Google Jobs",
  "source.linkedin_posts": "LinkedIn — Hiring posts",
  "source.linkedin_posts.hint": "At most once a day · requires sign-in",
  "source.linkedin_posts.short": "LinkedIn posts",
  "search.title_1": "1. What job are you looking for?",
  "search.keywords_hint": "Type a job title and press Enter. You can add more than one.",
  "search.keywords_placeholder": "Add a job title…",
  "search.keywords_tip": "Tip: job sites mostly use English job titles.",
  "search.title_2": "2. Where?",
  "search.locations_hint": "Choose countries, or Remote.",
  "search.locations_placeholder": "Search for a country…",
  "search.title_3": "3. Which sites?",
  "search.browser_needed": "Requires Google Chrome",
  "search.posts_warning": "LinkedIn posts use your personal account. Frequent searches may lead LinkedIn to restrict the account, so this option is off by default.",
  "search.title_4": "4. Automatic search",
  "search.auto_label": "Search automatically every",
  "search.auto_note": "(while the app is open) · you get a notification when new jobs are found",
  "search.save_and_run": "Save and search",
  "search.save_only": "Save only",
  "search.saved": "Your search settings were saved.",
  "hours.2": "2 hours",
  "hours.4": "4 hours",
  "hours.6": "6 hours",
  "hours.12": "12 hours",
  "hours.24": "24 hours",
  "estimate.minutes.one": "Estimated time: about 1 minute",
  "estimate.minutes.two": "Estimated time: about 2 minutes",
  "estimate.minutes.few": "Estimated time: about {n} minutes",
  "estimate.minutes.many": "Estimated time: about {n} minutes",
  "estimate.minutes.other": "Estimated time: about {n} minutes",
  "errors.keywords.required": "Add at least one job title.",
  "errors.keywords.too_long": "Each job title must be 100 characters or fewer.",
  "errors.keywords.too_many": "You can add up to 20 job titles.",
  "errors.keywords.invalid": "Please check the job titles.",
  "errors.locations.required": "Choose at least one country, or Remote.",
  "errors.locations.invalid": "One of the countries is not recognised. Remove it and choose it again.",
  "errors.sources.required": "Choose at least one site.",
  "errors.sources.invalid": "One of the sites is not recognised. Choose the sites again.",
  "errors.field.invalid": "Please check this field.",
  "linkedin.login": "Sign in to LinkedIn",
  "linkedin.opened": "Chrome opened on the LinkedIn sign-in page. Sign in, then close that window.",
  "notice.posts_skipped": "LinkedIn posts were not searched this time: they were already searched in the last 24 hours.",
  "notice.login_required": "LinkedIn posts require you to sign in.",
  "notice.browser.chrome_not_found": "Google Chrome was not found, so only LinkedIn and Indeed were searched.",
  "notice.browser.start_failed": "The browser could not be started, so Bayt, NaukriGulf and Google Jobs were skipped in this search.",
  "notice.browser.crashed": "The browser stopped unexpectedly, so the remaining sites were skipped in this search.",
  "notice.run_done": "Search finished. New jobs: {new}",
  "notice.run_cancelled": "Search stopped. New jobs: {new}",
  "notice.run_failed": "The search ended with an error. Details are in History.",
  "error.unexpected": "An unexpected error occurred.",
  "error.no_settings": "Set up your search first.",
  "error.run_in_progress": "A search is already running.",
  "error.not_running": "No search is running.",
  "error.chrome_not_found": "Google Chrome was not found.",
  "error.outside_data_dir": "The data could not be deleted safely. Open the data folder to remove it manually.",
  "error.files_in_use": "Some data files are in use. Close other Hawkeye Jobs windows and try again.",
  "error.not_found": "This item no longer exists.",
  "error.forbidden": "The connection to the app was refused. Close the app and open it again.",
  "error.offline_app": "The app is not responding. Close it and open it again.",
  "error.report_failed": "The problem report could not be prepared.",
  "action.report_problem": "Report a problem",
  "action.download_chrome": "Download Chrome",
  "action.undo": "Undo",
  "action.cancel": "Cancel",
  "action.close": "Close",
  "action.next": "Next",
  "action.back": "Back",
  "action.remove_item": "Remove {item}",
  "history.empty": "No searches yet.",
  "history.select_hint": "Select a search to see each site’s result.",
  "history.trigger.manual": "Manual",
  "history.trigger.auto": "Automatic",
  "history.trigger.terminal": "Terminal",
  "history.state.running": "Running",
  "history.state.done": "Finished",
  "history.state.cancelled": "Stopped",
  "history.state.failed": "Failed",
  "history.counts": "Found: {found} · New: {new}",
  "history.details_title": "Details: {when}",
  "history.no_details": "No per-site details were recorded for this search.",
  "history.query": "{source} · “{keyword}”",
  "history.query_location": "{source} · “{keyword}” in {location}",
  "history.q.done": "Found: {found} · New: {new}",
  "history.q.failed": "Temporarily unreachable",
  "history.q.skipped.login_required": "Sign-in required",
  "history.q.skipped.browser_unavailable": "Skipped: the browser was not available",
  "history.q.skipped.other": "Skipped",
  "settings.language": "Language",
  "settings.theme": "Appearance",
  "theme.auto": "Automatic",
  "theme.light": "Light",
  "theme.dark": "Dark",
  "settings.browser": "Browser",
  "settings.chrome_found": "✓ Google Chrome is installed",
  "settings.chrome_missing": "Google Chrome was not found. Only LinkedIn and Indeed will be searched.",
  "settings.linkedin": "LinkedIn account",
  "settings.linkedin_ready": "A LinkedIn sign-in is saved on this computer.",
  "settings.linkedin_none": "Not signed in.",
  "settings.version": "Version {version}",
  "settings.check_updates": "Check for updates",
  "settings.download_update": "Download the update",
  "settings.update_check": "Check for updates once a day",
  "settings.update_available": "Version {version} is available.",
  "settings.up_to_date": "You have the latest version.",
  "settings.update_disabled": "The daily update check is off.",
  "settings.update_unknown": "Could not check for updates right now.",
  "settings.problem": "Something not working?",
  "settings.report_opened": "A problem report opened in your browser. Review it, then submit it.",
  "settings.data_folder": "Data folder",
  "settings.open_folder": "Open folder",
  "settings.delete_all": "Delete all my data",
  "settings.delete": "Delete…",
  "settings.delete_title": "Delete all your data?",
  "settings.delete_body": "All saved jobs and the search history will be deleted from this computer. Your search settings are kept. This cannot be undone.",
  "settings.delete_type": "Type “{word}” to confirm.",
  "settings.delete_word": "DELETE",
  "settings.delete_confirm": "Delete permanently",
  "settings.deleted": "Your data was deleted.",
  "wizard.step": "Step {n} of {total}",
  "wizard.welcome": "Welcome to Hawkeye Jobs",
  "wizard.intro": "A few short steps, then we start looking for your next job.",
  "wizard.language_title": "Choose the app language",
  "wizard.browser_title": "Checking the browser",
  "wizard.chrome_ok": "Google Chrome was found. All sites are available.",
  "wizard.chrome_missing": "Google Chrome was not found. Only LinkedIn and Indeed will be searched. You can install Chrome and check again.",
  "wizard.recheck": "Check again",
  "wizard.ready_title": "Ready to start",
  "wizard.ready_body": "The first search takes a few minutes. You can keep using the app while it runs; new jobs appear as soon as they are found.",
  "wizard.start": "Start the first search"
}
```

Create `src/web/static/i18n/ar.json` (same keys, formal Arabic):

```json
{
  "app.loading": "جارٍ التحميل…",
  "nav.label": "التنقل الرئيسي",
  "nav.jobs": "الوظائف",
  "nav.search": "البحث",
  "nav.history": "السجل",
  "nav.settings": "الإعدادات",
  "run.start": "ابدأ البحث",
  "run.stop": "إيقاف البحث",
  "run.stopping": "جارٍ الإيقاف…",
  "banner.starting": "جارٍ تجهيز البحث…",
  "banner.progress": "جارٍ البحث… {done} من {total}",
  "banner.new": "الوظائف الجديدة حتى الآن: {n}",
  "banner.now": "الآن: {source} · «{keyword}» في {location}",
  "banner.now_nolocation": "الآن: {source} · «{keyword}»",
  "jobs.filter.new": "جديدة",
  "jobs.filter.saved": "محفوظة",
  "jobs.filter.applied": "تقدّمت",
  "jobs.filter.all": "الكل",
  "jobs.search_placeholder": "ابحث في المسمّيات والشركات…",
  "jobs.list_label": "الوظائف",
  "jobs.open": "فتح الإعلان",
  "jobs.save": "حفظ",
  "jobs.applied": "تقدّمت",
  "jobs.dismiss": "غير مهتم",
  "jobs.load_more": "عرض المزيد",
  "jobs.empty.new": "لا توجد وظائف جديدة. ابدأ البحث للعثور على المزيد.",
  "jobs.empty.saved": "لم تحفظ أي وظيفة بعد.",
  "jobs.empty.applied": "لم تُعلّم أي وظيفة بأنك تقدّمت إليها بعد.",
  "jobs.empty.all": "لا توجد وظائف بعد. حدّد ما تبحث عنه، ثم ابدأ البحث.",
  "jobs.empty.text": "لا توجد وظائف تطابق بحثك.",
  "jobs.dismissed_badge": "غير مهتم",
  "jobs.dismissed_toast": "أُخفيت الوظيفة.",
  "jobs.found_age": "وُجدت {age}",
  "age.now": "الآن",
  "age.minutes.one": "قبل دقيقة",
  "age.minutes.two": "قبل دقيقتين",
  "age.minutes.few": "قبل {n} دقائق",
  "age.minutes.many": "قبل {n} دقيقة",
  "age.minutes.other": "قبل {n} دقيقة",
  "age.hours.one": "قبل ساعة",
  "age.hours.two": "قبل ساعتين",
  "age.hours.few": "قبل {n} ساعات",
  "age.hours.many": "قبل {n} ساعة",
  "age.hours.other": "قبل {n} ساعة",
  "age.days.one": "قبل يوم",
  "age.days.two": "قبل يومين",
  "age.days.few": "قبل {n} أيام",
  "age.days.many": "قبل {n} يومًا",
  "age.days.other": "قبل {n} يوم",
  "source.linkedin": "LinkedIn — الوظائف",
  "source.linkedin.hint": "جميع الدول",
  "source.linkedin.short": "LinkedIn",
  "source.indeed": "Indeed",
  "source.indeed.hint": "لا يشمل الأردن والعمل عن بُعد",
  "source.indeed.short": "Indeed",
  "source.bayt": "Bayt",
  "source.bayt.hint": "الأردن ودول الخليج فقط",
  "source.bayt.short": "Bayt",
  "source.naukrigulf": "NaukriGulf",
  "source.naukrigulf.hint": "دول الخليج فقط",
  "source.naukrigulf.short": "NaukriGulf",
  "source.google_jobs": "Google Jobs",
  "source.google_jobs.hint": "يجمع وظائف من مواقع كثيرة",
  "source.google_jobs.short": "Google Jobs",
  "source.linkedin_posts": "LinkedIn — منشورات التوظيف",
  "source.linkedin_posts.hint": "مرة واحدة يوميًا كحد أقصى · يتطلب تسجيل الدخول",
  "source.linkedin_posts.short": "منشورات LinkedIn",
  "search.title_1": "١. ما الوظيفة التي تبحث عنها؟",
  "search.keywords_hint": "اكتب المسمّى الوظيفي واضغط Enter. يمكنك إضافة أكثر من مسمّى.",
  "search.keywords_placeholder": "أضف مسمّى وظيفيًا…",
  "search.keywords_tip": "نصيحة: مواقع التوظيف تستخدم المسمّيات بالإنجليزية غالبًا.",
  "search.title_2": "٢. أين؟",
  "search.locations_hint": "اختر الدول، أو «عن بُعد».",
  "search.locations_placeholder": "ابحث عن دولة…",
  "search.title_3": "٣. في أي المواقع؟",
  "search.browser_needed": "يتطلب Google Chrome",
  "search.posts_warning": "منشورات LinkedIn تستخدم حسابك الشخصي. البحث المتكرر قد يدفع LinkedIn إلى تقييد الحساب، لذلك هذا الخيار معطّل افتراضيًا.",
  "search.title_4": "٤. البحث التلقائي",
  "search.auto_label": "ابحث تلقائيًا كل",
  "search.auto_note": "(ما دام التطبيق مفتوحًا) · يصلك إشعار عند وجود وظائف جديدة",
  "search.save_and_run": "حفظ وابدأ البحث",
  "search.save_only": "حفظ فقط",
  "search.saved": "حُفظت إعدادات البحث.",
  "hours.2": "ساعتين",
  "hours.4": "4 ساعات",
  "hours.6": "6 ساعات",
  "hours.12": "12 ساعة",
  "hours.24": "24 ساعة",
  "estimate.minutes.one": "المدة التقديرية: نحو دقيقة واحدة",
  "estimate.minutes.two": "المدة التقديرية: نحو دقيقتين",
  "estimate.minutes.few": "المدة التقديرية: نحو {n} دقائق",
  "estimate.minutes.many": "المدة التقديرية: نحو {n} دقيقة",
  "estimate.minutes.other": "المدة التقديرية: نحو {n} دقيقة",
  "errors.keywords.required": "أضف مسمّى وظيفيًا واحدًا على الأقل.",
  "errors.keywords.too_long": "يجب ألا يزيد المسمّى الوظيفي على 100 حرف.",
  "errors.keywords.too_many": "يمكنك إضافة 20 مسمّى وظيفيًا كحد أقصى.",
  "errors.keywords.invalid": "يُرجى مراجعة المسمّيات الوظيفية.",
  "errors.locations.required": "اختر دولة واحدة على الأقل، أو «عن بُعد».",
  "errors.locations.invalid": "إحدى الدول غير معروفة. أزلها ثم اخترها من جديد.",
  "errors.sources.required": "اختر موقعًا واحدًا على الأقل.",
  "errors.sources.invalid": "أحد المواقع غير معروف. اختر المواقع من جديد.",
  "errors.field.invalid": "يُرجى مراجعة هذا الحقل.",
  "linkedin.login": "تسجيل الدخول إلى LinkedIn",
  "linkedin.opened": "فُتح Chrome على صفحة تسجيل الدخول إلى LinkedIn. سجّل الدخول، ثم أغلق تلك النافذة.",
  "notice.posts_skipped": "لم يُبحث في منشورات LinkedIn هذه المرة، لأنه بُحث فيها خلال آخر 24 ساعة.",
  "notice.login_required": "منشورات LinkedIn تتطلب تسجيل الدخول.",
  "notice.browser.chrome_not_found": "لم يُعثر على Google Chrome، لذلك اقتصر البحث على LinkedIn وIndeed.",
  "notice.browser.start_failed": "تعذّر تشغيل المتصفّح، لذلك تخطّينا Bayt وNaukriGulf وGoogle Jobs في هذا البحث.",
  "notice.browser.crashed": "توقّف المتصفّح على نحو غير متوقع، لذلك تخطّينا بقية المواقع في هذا البحث.",
  "notice.run_done": "انتهى البحث. الوظائف الجديدة: {new}",
  "notice.run_cancelled": "أُوقف البحث. الوظائف الجديدة: {new}",
  "notice.run_failed": "انتهى البحث بخطأ. التفاصيل في صفحة «السجل».",
  "error.unexpected": "حدث خطأ غير متوقع.",
  "error.no_settings": "حدّد ما تبحث عنه أولًا.",
  "error.run_in_progress": "يوجد بحث قيد التنفيذ بالفعل.",
  "error.not_running": "لا يوجد بحث قيد التنفيذ.",
  "error.chrome_not_found": "لم يُعثر على Google Chrome.",
  "error.outside_data_dir": "تعذّر حذف البيانات بأمان. افتح مجلّد البيانات لحذفها يدويًا.",
  "error.files_in_use": "بعض ملفات البيانات قيد الاستخدام. أغلق نوافذ Hawkeye Jobs الأخرى، ثم حاول مجددًا.",
  "error.not_found": "هذا العنصر لم يعد موجودًا.",
  "error.forbidden": "رُفض الاتصال بالتطبيق. أغلق التطبيق، ثم افتحه من جديد.",
  "error.offline_app": "التطبيق لا يستجيب. أغلقه، ثم افتحه من جديد.",
  "error.report_failed": "تعذّر تجهيز تقرير المشكلة.",
  "action.report_problem": "الإبلاغ عن مشكلة",
  "action.download_chrome": "تنزيل Chrome",
  "action.undo": "تراجع",
  "action.cancel": "إلغاء",
  "action.close": "إغلاق",
  "action.next": "التالي",
  "action.back": "رجوع",
  "action.remove_item": "إزالة {item}",
  "history.empty": "لا توجد عمليات بحث بعد.",
  "history.select_hint": "اختر عملية بحث لعرض نتيجة كل موقع.",
  "history.trigger.manual": "يدوي",
  "history.trigger.auto": "تلقائي",
  "history.trigger.terminal": "من الطرفية",
  "history.state.running": "قيد التنفيذ",
  "history.state.done": "اكتمل",
  "history.state.cancelled": "أُوقف",
  "history.state.failed": "فشل",
  "history.counts": "وُجدت: {found} · جديدة: {new}",
  "history.details_title": "تفاصيل: {when}",
  "history.no_details": "لم تُسجَّل تفاصيل المواقع لهذا البحث.",
  "history.query": "{source} · «{keyword}»",
  "history.query_location": "{source} · «{keyword}» في {location}",
  "history.q.done": "وُجدت: {found} · جديدة: {new}",
  "history.q.failed": "تعذّر الوصول مؤقتًا",
  "history.q.skipped.login_required": "يلزم تسجيل الدخول",
  "history.q.skipped.browser_unavailable": "تُخطّي: المتصفّح غير متاح",
  "history.q.skipped.other": "تُخطّي",
  "settings.language": "اللغة",
  "settings.theme": "المظهر",
  "theme.auto": "تلقائي",
  "theme.light": "فاتح",
  "theme.dark": "داكن",
  "settings.browser": "المتصفّح",
  "settings.chrome_found": "✓ Google Chrome مثبّت",
  "settings.chrome_missing": "لم يُعثر على Google Chrome. سنبحث في LinkedIn وIndeed فقط.",
  "settings.linkedin": "حساب LinkedIn",
  "settings.linkedin_ready": "يوجد تسجيل دخول محفوظ إلى LinkedIn على هذا الجهاز.",
  "settings.linkedin_none": "لم تسجّل الدخول.",
  "settings.version": "الإصدار {version}",
  "settings.check_updates": "التحقق من التحديثات",
  "settings.download_update": "تنزيل التحديث",
  "settings.update_check": "التحقق من التحديثات مرة يوميًا",
  "settings.update_available": "الإصدار {version} متاح.",
  "settings.up_to_date": "لديك أحدث إصدار.",
  "settings.update_disabled": "التحقق اليومي من التحديثات متوقف.",
  "settings.update_unknown": "تعذّر التحقق من التحديثات الآن.",
  "settings.problem": "هل تواجه مشكلة؟",
  "settings.report_opened": "فُتح تقرير المشكلة في متصفّحك. راجعه، ثم أرسله.",
  "settings.data_folder": "مجلّد البيانات",
  "settings.open_folder": "فتح المجلّد",
  "settings.delete_all": "حذف جميع بياناتي",
  "settings.delete": "حذف…",
  "settings.delete_title": "هل تريد حذف جميع بياناتك؟",
  "settings.delete_body": "ستُحذف جميع الوظائف المحفوظة وسجل البحث من هذا الجهاز، وتبقى إعدادات البحث. لا يمكن التراجع عن ذلك.",
  "settings.delete_type": "اكتب «{word}» للتأكيد.",
  "settings.delete_word": "حذف",
  "settings.delete_confirm": "حذف نهائي",
  "settings.deleted": "حُذفت بياناتك.",
  "wizard.step": "الخطوة {n} من {total}",
  "wizard.welcome": "مرحبًا بك في Hawkeye Jobs",
  "wizard.intro": "خطوات قصيرة، ثم نبدأ البحث عن وظيفتك القادمة.",
  "wizard.language_title": "اختر لغة التطبيق",
  "wizard.browser_title": "التحقق من المتصفّح",
  "wizard.chrome_ok": "تم العثور على Google Chrome. جميع المواقع متاحة.",
  "wizard.chrome_missing": "لم يُعثر على Google Chrome. سنبحث في LinkedIn وIndeed فقط، ويمكنك تثبيت Chrome ثم التحقق مجددًا.",
  "wizard.recheck": "التحقق مجددًا",
  "wizard.ready_title": "كل شيء جاهز",
  "wizard.ready_body": "يستغرق البحث الأول بضع دقائق. يمكنك استخدام التطبيق أثناء البحث، وتظهر الوظائف الجديدة فور العثور عليها.",
  "wizard.start": "ابدأ البحث الأول"
}
```

- [ ] **Step 5: Run the guards to verify they pass**

Run: `venv/bin/python3 -m unittest tests.test_frontend -v 2>&1 | tail -15`. Expected:
- `OK`;
- `test_js_unit_tests_pass` is skipped ("no JS tests yet" or "node is not installed").

- [ ] **Step 6: Full suite and commit**

Run: `venv/bin/python3 -m unittest discover -s tests 2>&1 | tail -3`. Expected: `OK`.

```bash
git add src/web/static/vendor src/web/static/i18n tests/test_frontend.py
git commit -m "Vendor Alpine.js 3.14.9 and add the Arabic/English UI dictionaries with static guards"
```

---

### Task 3: `logic.js` and `api.js` (pure helpers + token handling) with Node unit tests

**Files:**
- Create: `src/web/static/logic.js`, `src/web/static/api.js`, `tests/js/logic.test.mjs`, `tests/js/api.test.mjs`
- Modify: `tests/test_frontend.py` (one test: the JS constants mirror the Python ones)

**Interfaces:**
- Produces (used by Task 4). `logic.js` exports:
  - constants: `STATUSES`, `FILTERS`, `SOURCE_ORDER`, `BROWSER_SOURCES`, `AUTO_HOURS`, `MAX_KEYWORDS`, `MAX_KEYWORD_LENGTH`;
  - text: `format(template, params)`, `translate(dict, fallback, key, params)`, `pluralKey(dict, base, n, lang)`;
  - run progress: `ageParts(iso, nowMs) -> {base, n} | null`, `startingRun()`, `applyEvent(run, event)`, `fromSnapshot(snapshot, previous)`, `progressPercent(run)`;
  - search form: `addKeyword(list, raw) -> {list, error}`, `filterCountries(countries, query, selected, limit=50)`, `defaultSources(chromeFound)`, `orderSources(ids)`;
  - jobs: `jobsQuery({filter, text, offset, limit})`, `jobSourceId(source)`, `nextStatus(current, pressed)`, `jobVisible(status, filter)`;
  - keys and labels: `apiErrorKey(status, body)`, `queryOutcome(query) -> {key, params}`, `estimateMinutes(seconds)`, `runFinishedKey(state)`, `browserNoticeKey(reason)`.
- `api.js` exports `takeToken(win=window)`, `ApiError(status, body)` and `createApi(token, fetchImpl)`. `createApi` returns `{get, post, put, patch, events}`.

- [ ] **Step 1: Write the failing JS tests**

Create `tests/js/logic.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import * as L from "../../src/web/static/logic.js";

test("format fills named placeholders and leaves unknown ones", () => {
  assert.equal(L.format("{done} of {total}", { done: 3, total: 9 }), "3 of 9");
  assert.equal(L.format("{x}", {}), "{x}");
});

test("translate falls back to English, then to the key", () => {
  assert.equal(L.translate({ a: "ar" }, { a: "en", b: "en-b" }, "a"), "ar");
  assert.equal(L.translate({}, { b: "en-b {n}" }, "b", { n: 2 }), "en-b 2");
  assert.equal(L.translate({}, {}, "missing.key"), "missing.key");
});

test("pluralKey picks the Arabic form and falls back to other", () => {
  const dict = { "x.one": "", "x.two": "", "x.few": "", "x.many": "", "x.other": "" };
  assert.equal(L.pluralKey(dict, "x", 1, "ar"), "x.one");
  assert.equal(L.pluralKey(dict, "x", 2, "ar"), "x.two");
  assert.equal(L.pluralKey(dict, "x", 5, "ar"), "x.few");
  assert.equal(L.pluralKey(dict, "x", 11, "ar"), "x.many");
  assert.equal(L.pluralKey(dict, "x", 100, "ar"), "x.other");
  assert.equal(L.pluralKey(dict, "x", 1, "en"), "x.one");
  assert.equal(L.pluralKey({ "x.other": "" }, "x", 1, "en"), "x.other");
});

test("ageParts buckets minutes, hours and days", () => {
  const now = Date.parse("2026-10-10T12:00:00Z");
  assert.deepEqual(L.ageParts("2026-10-10T11:59:30Z", now), { base: "age.now", n: 0 });
  assert.deepEqual(L.ageParts("2026-10-10T11:55:00Z", now), { base: "age.minutes", n: 5 });
  assert.deepEqual(L.ageParts("2026-10-10T09:00:00Z", now), { base: "age.hours", n: 3 });
  assert.deepEqual(L.ageParts("2026-10-07T12:00:00Z", now), { base: "age.days", n: 3 });
  assert.equal(L.ageParts("not a date", now), null);
});

test("applyEvent tracks a run from start to finish", () => {
  let run = L.applyEvent(null, { type: "run_started", run_id: 7, total: 3, trigger: "auto" });
  assert.deepEqual(run, { run_id: 7, trigger: "auto", total: 3, done: 0, found: 0, new: 0, current: null, stopping: false });
  run = L.applyEvent(run, { type: "query_started", source: "bayt", keyword: "Java", location_id: "jo", index: 0 });
  assert.deepEqual(run.current, { source: "bayt", keyword: "Java", location_id: "jo" });
  run = L.applyEvent(run, { type: "query_done", found: 5, new: 2 });
  run = L.applyEvent(run, { type: "query_failed", error: "HTTPError" });
  run = L.applyEvent(run, { type: "query_skipped", reason: "login_required" });
  assert.equal(run.done, 3);
  assert.equal(run.found, 5);
  assert.equal(run.new, 2);
  assert.equal(L.applyEvent(run, { type: "jobs_reported", count: 2 }), run);
  assert.equal(L.applyEvent(run, { type: "run_finished", state: "done" }), null);
});

test("applyEvent keeps stopping, ignores query events without a run, clears on run_error", () => {
  const run = { ...L.startingRun(), run_id: 1, total: 2, stopping: true };
  assert.equal(L.applyEvent(run, { type: "query_done", found: 1, new: 0 }).stopping, true);
  assert.equal(L.applyEvent(null, { type: "query_done", found: 1, new: 1 }), null);
  assert.equal(L.applyEvent(run, { type: "run_error" }), null);
});

test("fromSnapshot keeps the current query of the same run and the starting placeholder", () => {
  const prev = { run_id: 4, trigger: "manual", total: 9, done: 1, found: 0, new: 0,
                 current: { source: "linkedin", keyword: "Java", location_id: "jo" }, stopping: false };
  const snap = { run_id: 4, trigger: "manual", total: 9, done: 2, found: 3, new: 1 };
  assert.deepEqual(L.fromSnapshot(snap, prev), { ...snap, current: prev.current, stopping: false });
  assert.equal(L.fromSnapshot(snap, null).current, null);
  assert.equal(L.fromSnapshot({ ...snap, run_id: 5 }, prev).current, null);
  const starting = L.startingRun();
  assert.equal(L.fromSnapshot(null, starting), starting);
  assert.equal(L.fromSnapshot(null, prev), null);
});

test("progressPercent is bounded", () => {
  assert.equal(L.progressPercent(null), 0);
  assert.equal(L.progressPercent({ done: 3, total: 4 }), 75);
  assert.equal(L.progressPercent({ done: 5, total: 4 }), 100);
  assert.equal(L.progressPercent({ done: 0, total: 0 }), 0);
});

test("addKeyword trims, dedupes case-insensitively and enforces limits", () => {
  assert.deepEqual(L.addKeyword([], "  Java   Developer "), { list: ["Java Developer"], error: null });
  assert.deepEqual(L.addKeyword(["Java"], "java"), { list: ["Java"], error: null });
  assert.deepEqual(L.addKeyword([], "x".repeat(101)), { list: [], error: "too_long" });
  const twenty = Array.from({ length: 20 }, (_, i) => `k${i}`);
  assert.equal(L.addKeyword(twenty, "new").error, "too_many");
  assert.deepEqual(L.addKeyword(["a"], "   "), { list: ["a"], error: null });
});

test("filterCountries matches labels and ids, skips chosen ones, caps the list", () => {
  const countries = [{ id: "jo", label: "الأردن" }, { id: "sa", label: "السعودية" }, { id: "remote", label: "عن بُعد" }];
  assert.deepEqual(L.filterCountries(countries, "الأ", []).map((c) => c.id), ["jo"]);
  assert.deepEqual(L.filterCountries(countries, "SA", []).map((c) => c.id), ["sa"]);
  assert.deepEqual(L.filterCountries(countries, "", ["jo"]).map((c) => c.id), ["sa", "remote"]);
  assert.equal(L.filterCountries(countries, "", [], 1).length, 1);
});

test("jobsQuery builds the API path", () => {
  assert.equal(L.jobsQuery({ filter: "new", text: " java ", offset: 0, limit: 50 }),
               "/api/jobs?status=new&text=java&limit=50&offset=0");
  assert.equal(L.jobsQuery({ filter: "all", text: "", offset: 50, limit: 50 }),
               "/api/jobs?status=all&limit=50&offset=50");
});

test("jobSourceId maps stored source names", () => {
  assert.equal(L.jobSourceId("Linkedin"), "linkedin");
  assert.equal(L.jobSourceId("Indeed"), "indeed");
  assert.equal(L.jobSourceId("Bayt"), "bayt");
  assert.equal(L.jobSourceId("NaukriGulf"), "naukrigulf");
  assert.equal(L.jobSourceId("Google Jobs"), "google_jobs");
  assert.equal(L.jobSourceId("LinkedIn Post"), "linkedin_posts");
  assert.equal(L.jobSourceId("Something else"), null);
});

test("statuses toggle back to new and decide visibility", () => {
  assert.equal(L.nextStatus("new", "saved"), "saved");
  assert.equal(L.nextStatus("saved", "saved"), "new");
  assert.equal(L.nextStatus("dismissed", "new"), "new");
  assert.equal(L.jobVisible("saved", "new"), false);
  assert.equal(L.jobVisible("saved", "saved"), true);
  assert.equal(L.jobVisible("dismissed", "all"), true);
});

test("default sources depend on Chrome and orderSources follows the catalog order", () => {
  assert.deepEqual(L.defaultSources(true), ["linkedin", "indeed", "bayt", "naukrigulf", "google_jobs"]);
  assert.deepEqual(L.defaultSources(false), ["linkedin", "indeed"]);
  assert.deepEqual(L.orderSources(["google_jobs", "linkedin", "nope", "linkedin"]), ["linkedin", "google_jobs"]);
});

test("apiErrorKey maps known codes and hides unknown ones", () => {
  assert.equal(L.apiErrorKey(409, { error: "run_in_progress" }), "error.run_in_progress");
  assert.equal(L.apiErrorKey(404, { detail: { job: "not_found" } }), "error.not_found");
  assert.equal(L.apiErrorKey(0, {}), "error.offline_app");
  assert.equal(L.apiErrorKey(500, { error: "http_500" }), "error.unexpected");
  assert.equal(L.apiErrorKey(409, { error: "something_new" }), "error.unexpected");
  assert.equal(L.apiErrorKey(500, null), "error.unexpected");
});

test("queryOutcome turns stored rows into keys", () => {
  assert.deepEqual(L.queryOutcome({ status: "done", found: 3, new: 1 }), { key: "history.q.done", params: { found: 3, new: 1 } });
  assert.equal(L.queryOutcome({ status: "failed", detail: "HTTPError" }).key, "history.q.failed");
  assert.equal(L.queryOutcome({ status: "skipped", detail: "login_required" }).key, "history.q.skipped.login_required");
  assert.equal(L.queryOutcome({ status: "skipped", detail: "weird" }).key, "history.q.skipped.other");
});

test("small key helpers", () => {
  assert.equal(L.estimateMinutes(20), 1);
  assert.equal(L.estimateMinutes(90), 2);
  assert.equal(L.estimateMinutes(600), 10);
  assert.equal(L.runFinishedKey("done"), "notice.run_done");
  assert.equal(L.runFinishedKey("cancelled"), "notice.run_cancelled");
  assert.equal(L.runFinishedKey("failed"), "notice.run_failed");
  assert.equal(L.browserNoticeKey("crashed"), "notice.browser.crashed");
  assert.equal(L.browserNoticeKey("odd"), "notice.browser.start_failed");
});
```

Create `tests/js/api.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApi, takeToken } from "../../src/web/static/api.js";

function fakeWindow(href, stored = null, { blocked = false } = {}) {
  const storage = new Map(stored ? [["hawkeye-token", stored]] : []);
  const win = {
    location: { href },
    sessionStorage: {
      getItem: (key) => { if (blocked) throw new Error("blocked"); return storage.has(key) ? storage.get(key) : null; },
      setItem: (key, value) => { if (blocked) throw new Error("blocked"); storage.set(key, value); },
    },
    history: { replaceState: (_state, _title, url) => { win.replaced = url; } },
    replaced: null,
  };
  return { win, storage };
}

test("takeToken moves the token from the URL into session storage", () => {
  const { win, storage } = fakeWindow("http://127.0.0.1:5555/?token=abc#jobs");
  assert.equal(takeToken(win), "abc");
  assert.equal(storage.get("hawkeye-token"), "abc");
  assert.equal(win.replaced, "/#jobs");
});

test("takeToken reads session storage after a reload", () => {
  const { win } = fakeWindow("http://127.0.0.1:5555/", "abc");
  assert.equal(takeToken(win), "abc");
  assert.equal(win.replaced, null);
});

test("takeToken survives blocked storage", () => {
  assert.equal(takeToken(fakeWindow("http://127.0.0.1:5555/?token=abc", null, { blocked: true }).win), "abc");
  assert.equal(takeToken(fakeWindow("http://127.0.0.1:5555/", null, { blocked: true }).win), "");
});

test("createApi sends the token and JSON, and turns failures into ApiError", async () => {
  const calls = [];
  const fetchImpl = async (path, options) => {
    calls.push({ path, options });
    if (path === "/api/fail") return { ok: false, status: 409, json: async () => ({ error: "run_in_progress" }) };
    if (path === "/api/empty") return { ok: true, status: 204, json: async () => { throw new Error("no body"); } };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const api = createApi("tok", fetchImpl);
  assert.deepEqual(await api.patch("/api/jobs/1", { status: "saved" }), { ok: true });
  assert.equal(calls[0].options.method, "PATCH");
  assert.equal(calls[0].options.headers["X-Hawkeye-Token"], "tok");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.body, JSON.stringify({ status: "saved" }));
  assert.equal(await api.post("/api/empty"), null);
  assert.equal(calls[1].options.body, "{}");
  const get = await api.get("/api/state");
  assert.deepEqual(get, { ok: true });
  assert.equal(calls[2].options.body, undefined);
  assert.equal(calls[2].options.headers["Content-Type"], undefined);
  await assert.rejects(api.get("/api/fail"),
    (error) => error instanceof ApiError && error.status === 409 && error.body.error === "run_in_progress");
  await assert.rejects(createApi("tok", async () => { throw new TypeError("network"); }).get("/api/state"),
    (error) => error instanceof ApiError && error.status === 0);
});
```

Append to `tests/test_frontend.py`, inside `class SafetyTests`:

```python
    def test_js_constants_mirror_the_backend(self):
        logic = _code("logic.js")

        def array(name):
            match = re.search(r"export const %s = \[([^\]]*)\]" % name, logic)
            self.assertIsNotNone(match, name)
            return [item.strip().strip("\"'") for item in match.group(1).split(",") if item.strip()]

        from catalog import BROWSER_SOURCES
        self.assertEqual(array("SOURCE_ORDER"), list(SOURCE_ORDER))
        self.assertEqual(set(array("BROWSER_SOURCES")), set(BROWSER_SOURCES))
        self.assertEqual([int(h) for h in array("AUTO_HOURS")], list(AUTO_HOURS))
```

- [ ] **Step 2: Run them to verify they fail**

Run:
- `node --test tests/js/logic.test.mjs tests/js/api.test.mjs 2>&1 | tail -5`. Expected: `ERR_MODULE_NOT_FOUND` for `logic.js` / `api.js`.
- `venv/bin/python3 -m unittest tests.test_frontend 2>&1 | tail -3`. Expected: `FAILED` in `test_js_constants_mirror_the_backend` and `test_js_unit_tests_pass`.

- [ ] **Step 3: Write `logic.js`**

Create `src/web/static/logic.js`:

```js
/*
 * logic.js — Pure helpers for the Hawkeye Jobs window (no DOM, no network).
 * Unit-tested with `node --test tests/js/`. The constants mirror the backend
 * (tests/test_frontend.py checks SOURCE_ORDER, BROWSER_SOURCES, AUTO_HOURS).
 */

export const STATUSES = ["new", "saved", "applied", "dismissed"];
export const FILTERS = ["new", "saved", "applied", "all"];
export const SOURCE_ORDER = ["linkedin", "indeed", "bayt", "naukrigulf", "google_jobs", "linkedin_posts"];
export const BROWSER_SOURCES = ["bayt", "naukrigulf", "google_jobs", "linkedin_posts"];
export const AUTO_HOURS = [2, 4, 6, 12, 24];
export const MAX_KEYWORDS = 20;
export const MAX_KEYWORD_LENGTH = 100;

const has = (object, key) => !!object && Object.prototype.hasOwnProperty.call(object, key);

// ── text ─────────────────────────────────────────────────────────────

export function format(template, params = {}) {
  return String(template).replace(/\{(\w+)\}/g, (match, name) => (has(params, name) ? String(params[name]) : match));
}

export function translate(dict, fallback, key, params = {}) {
  const text = has(dict, key) ? dict[key] : has(fallback, key) ? fallback[key] : key;
  return format(text, params);
}

export function pluralKey(dict, base, n, lang) {
  let rule = "other";
  try {
    rule = new Intl.PluralRules(lang).select(n);
  } catch (error) {
    rule = n === 1 ? "one" : "other";
  }
  const key = `${base}.${rule}`;
  return has(dict, key) ? key : `${base}.other`;
}

export function ageParts(iso, nowMs) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const minutes = Math.max(0, Math.floor((nowMs - then) / 60000));
  if (minutes < 1) return { base: "age.now", n: 0 };
  if (minutes < 60) return { base: "age.minutes", n: minutes };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { base: "age.hours", n: hours };
  return { base: "age.days", n: Math.floor(hours / 24) };
}

// ── run progress ─────────────────────────────────────────────────────

export function startingRun() {
  return { run_id: null, trigger: "manual", total: 0, done: 0, found: 0, new: 0, current: null, stopping: false };
}

export function applyEvent(run, event) {
  switch (event.type) {
    case "run_started":
      return { run_id: event.run_id, trigger: event.trigger || "manual", total: event.total || 0,
               done: 0, found: 0, new: 0, current: null, stopping: false };
    case "query_started":
      return run ? { ...run, current: { source: event.source, keyword: event.keyword, location_id: event.location_id } } : run;
    case "query_done":
    case "query_failed":
    case "query_skipped":
      if (!run) return run;
      return { ...run, done: run.done + 1, found: run.found + (Number(event.found) || 0),
               new: run.new + (Number(event.new) || 0) };
    case "run_finished":
    case "run_error":
      return null;
    default:
      return run;
  }
}

// GET /api/state -> run snapshot {run_id, trigger, total, done, found, new} (no current query).
export function fromSnapshot(snapshot, previous) {
  if (!snapshot) return previous && previous.run_id === null ? previous : null;
  const same = !!previous && previous.run_id === snapshot.run_id;
  return {
    run_id: snapshot.run_id, trigger: snapshot.trigger, total: snapshot.total, done: snapshot.done,
    found: snapshot.found, new: snapshot.new,
    current: same ? previous.current : null, stopping: same ? previous.stopping : false,
  };
}

export function progressPercent(run) {
  if (!run || !run.total) return 0;
  return Math.min(100, Math.round((run.done * 100) / run.total));
}

// ── search form ──────────────────────────────────────────────────────

export function addKeyword(list, raw) {
  const text = String(raw || "").replace(/\s+/g, " ").trim();
  if (!text) return { list, error: null };
  if (text.length > MAX_KEYWORD_LENGTH) return { list, error: "too_long" };
  if (list.some((keyword) => keyword.toLowerCase() === text.toLowerCase())) return { list, error: null };
  if (list.length >= MAX_KEYWORDS) return { list, error: "too_many" };
  return { list: [...list, text], error: null };
}

export function filterCountries(countries, query, selected, limit = 50) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  const chosen = new Set(selected);
  return countries
    .filter((c) => !chosen.has(c.id) && (!needle || c.label.toLocaleLowerCase().includes(needle) || c.id.toLowerCase() === needle))
    .slice(0, limit);
}

export function defaultSources(chromeFound) {
  return SOURCE_ORDER.filter((id) => id !== "linkedin_posts" && (chromeFound || !BROWSER_SOURCES.includes(id)));
}

export function orderSources(ids) {
  return SOURCE_ORDER.filter((id) => ids.includes(id));
}

// ── jobs ─────────────────────────────────────────────────────────────

export function jobsQuery({ filter, text, offset, limit }) {
  const params = new URLSearchParams();
  params.set("status", filter); // "all" includes "not interested" (the API handles it)
  if (text && text.trim()) params.set("text", text.trim());
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return `/api/jobs?${params.toString()}`;
}

const JOB_SOURCE_IDS = {
  linkedin: "linkedin", indeed: "indeed", bayt: "bayt", "bayt.com": "bayt", naukrigulf: "naukrigulf",
  "google jobs": "google_jobs", "linkedin post": "linkedin_posts",
};

export function jobSourceId(source) {
  return JOB_SOURCE_IDS[String(source || "").trim().toLowerCase()] || null;
}

export function nextStatus(current, pressed) {
  return current === pressed ? "new" : pressed;
}

export function jobVisible(status, filter) {
  return filter === "all" || status === filter;
}

// ── codes -> i18n keys ───────────────────────────────────────────────

const KNOWN_ERRORS = ["run_in_progress", "no_settings", "not_running", "chrome_not_found", "outside_data_dir",
                      "files_in_use", "not_found", "forbidden"];

export function apiErrorKey(status, body) {
  let code = body && body.error;
  if (!code && body && body.detail && typeof body.detail === "object") code = Object.values(body.detail)[0];
  if (KNOWN_ERRORS.includes(code)) return `error.${code}`;
  if (status === 0) return "error.offline_app";
  return "error.unexpected";
}

export function queryOutcome(query) {
  if (query.status === "done") return { key: "history.q.done", params: { found: query.found, new: query.new } };
  if (query.status === "failed") return { key: "history.q.failed", params: {} };
  const reason = ["login_required", "browser_unavailable"].includes(query.detail) ? query.detail : "other";
  return { key: `history.q.skipped.${reason}`, params: {} };
}

export function estimateMinutes(seconds) {
  return Math.max(1, Math.round(seconds / 60));
}

export function runFinishedKey(state) {
  if (state === "done") return "notice.run_done";
  if (state === "cancelled") return "notice.run_cancelled";
  return "notice.run_failed";
}

export function browserNoticeKey(reason) {
  return ["chrome_not_found", "start_failed", "crashed"].includes(reason) ? `notice.browser.${reason}` : "notice.browser.start_failed";
}
```

- [ ] **Step 4: Write `api.js`**

Create `src/web/static/api.js`:

```js
/*
 * api.js — Talks to the app's local server (127.0.0.1) with the per-launch token.
 * The token arrives once in the window URL; it is moved to sessionStorage and
 * removed from the address bar so it never shows up in a Referer or a screenshot.
 */

const TOKEN_KEY = "hawkeye-token";

export function takeToken(win = window) {
  const url = new URL(win.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    try {
      win.sessionStorage.setItem(TOKEN_KEY, fromUrl);
    } catch (error) {
      // Storage blocked: the token still works until the window is reloaded.
    }
    url.searchParams.delete("token");
    win.history.replaceState(null, "", url.pathname + url.search + url.hash);
    return fromUrl;
  }
  try {
    return win.sessionStorage.getItem(TOKEN_KEY) || "";
  } catch (error) {
    return "";
  }
}

export class ApiError extends Error {
  constructor(status, body) {
    super(`HTTP ${status}`);
    this.status = status; // 0 = the local server could not be reached
    this.body = body || {};
  }
}

export function createApi(token, fetchImpl = (...args) => window.fetch(...args)) {
  async function request(method, path, body) {
    const headers = { "X-Hawkeye-Token": token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetchImpl(path, {
        method, headers, cache: "no-store", body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      throw new ApiError(0, {});
    }
    if (response.status === 204) return null;
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok) throw new ApiError(response.status, data);
    return data;
  }

  return {
    get: (path) => request("GET", path),
    post: (path, body = {}) => request("POST", path, body),
    put: (path, body) => request("PUT", path, body),
    patch: (path, body) => request("PATCH", path, body),
    // EventSource cannot send headers; /api/events is the only endpoint that accepts ?token=.
    events: () => new EventSource(`/api/events?token=${encodeURIComponent(token)}`),
  };
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
- `node --test tests/js/logic.test.mjs tests/js/api.test.mjs 2>&1 | tail -8`. Expected: `# fail 0`.
- `venv/bin/python3 -m unittest tests.test_frontend -v 2>&1 | tail -5`. Expected: `OK`, and `test_js_unit_tests_pass` runs (not skipped) when node is installed.

- [ ] **Step 6: Full suite and commit**

Run: `venv/bin/python3 -m unittest discover -s tests 2>&1 | tail -3`. Expected: `OK`.

```bash
git add src/web/static/logic.js src/web/static/api.js tests/js tests/test_frontend.py
git commit -m "Add the UI's pure helpers and API client with Node unit tests"
```

---

### Task 4: The window (`index.html`, `styles.css`, `app.js`)

**Files:**
- Create: `src/web/static/app.js`, `src/web/static/styles.css`
- Replace: `src/web/static/index.html`
- Test: `tests/test_frontend.py` (the existing guards now scan the new files), `tests/test_api.py` (one test that the static UI is served)

**Interfaces:**
- Consumes:
  - `logic.js` and `api.js` (Task 3);
  - the dictionaries (Task 2);
  - `POST /api/estimate` and the security headers (Task 1);
  - every endpoint in `docs/ARCHITECTURE.md` → "Desktop app".
- Produces: the Alpine component `hawkeye` on `<body x-data="hawkeye">`. Task 6 drives it in the browser.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py` inside `class GuardTests`:

```python
    def test_static_ui_files_are_served_without_the_token(self):
        for path in ("/", "/app.js", "/logic.js", "/api.js", "/styles.css", "/i18n/ar.json",
                     "/vendor/alpine.min.js"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertIn('x-data="hawkeye"', self.client.get("/").text)
```

Run: `venv/bin/python3 -m unittest tests.test_api.GuardTests -v 2>&1 | tail -5`. Expected: FAIL (`/app.js` returns 404).

- [ ] **Step 2: Write `styles.css`**

Create `src/web/static/styles.css`:

```css
/*
 * styles.css — Hawkeye Jobs window.
 * Colour tokens with light/dark themes; layout uses logical properties only,
 * so the same rules serve Arabic (RTL) and English (LTR). Works down to 360 px.
 */

:root {
  --bg: #f8fafc;
  --surface: #ffffff;
  --surface-2: #f1f5f9;
  --border: #e2e8f0;
  --text: #0f172a;
  --muted: #64748b;
  --accent: #2563eb;
  --accent-text: #ffffff;
  --accent-soft: #eff6ff;
  --danger: #b91c1c;
  --ok: #15803d;
  --amber: #d97706;
  --warn-bg: #fffbeb;
  --warn-border: #fcd34d;
  --warn-text: #92400e;
  --badge-bg: #e0e7ff;
  --badge-text: #3730a3;
  --topbar: #0f172a;
  --topbar-text: #ffffff;
  --radius: 10px;
  --shadow: 0 1px 2px rgb(15 23 42 / 6%);
  color-scheme: light;
  font-family: system-ui, -apple-system, "Segoe UI", Tahoma, "Noto Sans Arabic", sans-serif;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0b1220;
    --surface: #111a2e;
    --surface-2: #18233a;
    --border: #263247;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #3b82f6;
    --accent-soft: #172554;
    --danger: #f87171;
    --ok: #4ade80;
    --amber: #fbbf24;
    --warn-bg: #2a2108;
    --warn-border: #a16207;
    --warn-text: #fcd34d;
    --badge-bg: #1e1b4b;
    --badge-text: #c7d2fe;
    --topbar: #020617;
    color-scheme: dark;
  }
}

:root[data-theme="dark"] {
  --bg: #0b1220;
  --surface: #111a2e;
  --surface-2: #18233a;
  --border: #263247;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #3b82f6;
  --accent-soft: #172554;
  --danger: #f87171;
  --ok: #4ade80;
  --amber: #fbbf24;
  --warn-bg: #2a2108;
  --warn-border: #a16207;
  --warn-text: #fcd34d;
  --badge-bg: #1e1b4b;
  --badge-text: #c7d2fe;
  --topbar: #020617;
  color-scheme: dark;
}

* { box-sizing: border-box; }
[x-cloak] { display: none !important; }
html, body { margin: 0; min-height: 100%; }
body { background: var(--bg); color: var(--text); font-size: 15px; line-height: 1.5; overflow-x: hidden; }
button, input, select { font: inherit; color: inherit; }
h1, h2, h3, p { overflow-wrap: anywhere; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button:disabled { opacity: .55; cursor: not-allowed; }

.boot, .fatal { display: grid; place-items: center; min-height: 100vh; padding: 24px; text-align: center; color: var(--muted); }

.shell {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  grid-template-rows: auto 1fr;
  grid-template-areas: "top top" "nav main";
  min-height: 100vh;
}
.shell.is-wizard { grid-template-columns: minmax(0, 1fr); grid-template-areas: "top" "main"; }

.topbar {
  grid-area: top; display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 16px; background: var(--topbar); color: var(--topbar-text); position: sticky; top: 0; z-index: 5;
}
.brand { font-weight: 700; font-size: 17px; }

.sidenav {
  grid-area: nav; display: flex; flex-direction: column; gap: 4px; padding: 12px 10px;
  background: var(--surface-2); border-inline-end: 1px solid var(--border);
}
.nav-item {
  display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 8px 12px;
  border: 0; border-radius: 8px; background: transparent; cursor: pointer; text-align: start;
}
.nav-item:hover { background: var(--surface); }
.nav-item[aria-current="page"] { background: var(--accent); color: var(--accent-text); font-weight: 600; }
.nav-count, .chip-count { font-size: 12px; border-radius: 999px; padding: 0 7px; background: var(--surface); color: var(--text); }
.nav-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.nav-item[aria-current="page"] .nav-dot { background: var(--accent-text); }

.main { grid-area: main; padding: 20px; max-width: 980px; width: 100%; }
.page-title { font-size: 22px; margin: 0 0 16px; }
.panel {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 14px 16px; margin-bottom: 14px; box-shadow: var(--shadow);
}
.panel h2 { font-size: 16px; margin: 0 0 4px; }
.hint, .tip { color: var(--muted); font-size: 13px; margin: 4px 0; }

.btn, .btn-ghost, .btn-danger, .btn-danger-ghost, .btn-small, .btn-ghost-small {
  border-radius: 8px; cursor: pointer; font-weight: 600; white-space: nowrap;
}
.btn { background: var(--accent); color: var(--accent-text); border: 1px solid var(--accent); padding: 7px 14px; }
.btn-ghost { background: var(--surface); color: var(--text); border: 1px solid var(--border); padding: 6px 12px; font-weight: 500; }
.btn-danger { background: var(--danger); color: #ffffff; border: 1px solid var(--danger); padding: 7px 14px; }
.btn-danger-ghost { background: transparent; color: var(--danger); border: 1px solid var(--danger); padding: 6px 12px; }
.btn-small { background: var(--accent); color: var(--accent-text); border: 1px solid var(--accent); padding: 4px 10px; font-size: 13px; }
.btn-ghost-small { background: var(--surface); border: 1px solid var(--border); padding: 4px 10px; font-size: 13px; font-weight: 500; }
.btn-ghost-small[aria-pressed="true"] { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }

.banner {
  background: var(--accent-soft); border: 1px solid var(--accent); border-radius: var(--radius);
  padding: 12px 14px; margin-bottom: 14px;
}
.banner-row { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.progress { height: 8px; background: var(--surface); border-radius: 999px; overflow: hidden; margin: 8px 0 4px; }
.progress-fill { height: 100%; width: 0; background: var(--accent); transition: width .3s ease; }

.toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  display: inline-flex; gap: 6px; align-items: center; border: 1px solid var(--border); background: var(--surface);
  border-radius: 999px; padding: 4px 12px; cursor: pointer;
}
.chip[aria-selected="true"] { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
.search-box, select, .dialog input {
  border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 6px 10px; min-width: 0;
}
.search-box { flex: 1 1 220px; max-width: 360px; }

.job-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.job { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; box-shadow: var(--shadow); }
.job.is-dismissed { opacity: .65; }
.job-head { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
.job-title { margin: 0; font-size: 16px; }
.badge { font-size: 12px; border-radius: 6px; padding: 1px 8px; background: var(--badge-bg); color: var(--badge-text); white-space: nowrap; }
.job-meta { margin: 4px 0 0; color: var(--muted); overflow-wrap: anywhere; }
.job-age { margin: 2px 0 0; color: var(--muted); font-size: 13px; display: flex; gap: 8px; flex-wrap: wrap; }
.tag-muted { border: 1px solid var(--border); border-radius: 999px; padding: 0 8px; }
.job-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.empty { color: var(--muted); text-align: center; padding: 30px 10px; }
.load-more { display: block; margin: 14px auto 0; }

.tags {
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center; border: 1px solid var(--border);
  border-radius: 8px; padding: 6px; background: var(--surface);
}
.tag {
  display: inline-flex; align-items: center; gap: 4px; background: var(--badge-bg); color: var(--badge-text);
  border-radius: 999px; padding-block: 2px; padding-inline: 10px 4px; max-width: 100%;
}
.tag > span { overflow-wrap: anywhere; }
.tag-remove { border: 0; background: transparent; color: inherit; cursor: pointer; font-size: 16px; line-height: 1; padding: 0 4px; }
.tag-input { border: 0; flex: 1 1 140px; padding: 4px; background: transparent; min-width: 0; }
.tag-input:focus-visible { outline: none; }
.tags:focus-within { outline: 2px solid var(--accent); outline-offset: 1px; }
.combo { position: relative; flex: 1 1 160px; display: flex; min-width: 0; }
.combo .tag-input { width: 100%; }
.combo-list {
  position: absolute; inset-inline-start: 0; top: 100%; z-index: 10; list-style: none; margin: 4px 0 0; padding: 4px;
  width: min(320px, 85vw); max-height: 260px; overflow: auto; background: var(--surface);
  border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 8px 24px rgb(15 23 42 / 15%);
}
.combo-list button { width: 100%; text-align: start; border: 0; background: transparent; padding: 6px 8px; border-radius: 6px; cursor: pointer; }
.combo-list button:hover, .combo-list button:focus-visible { background: var(--accent-soft); }
.field-error { color: var(--danger); font-size: 13px; margin: 6px 0 0; }

.sources { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(230px, 100%), 1fr)); gap: 8px; margin-top: 8px; }
.source { display: flex; gap: 10px; align-items: flex-start; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; cursor: pointer; }
.source input { margin-top: 4px; }
.source small { display: block; color: var(--muted); }
.source.is-disabled { opacity: .6; cursor: not-allowed; }
.warning {
  background: var(--warn-bg); border: 1px solid var(--warn-border); color: var(--warn-text); border-radius: 8px;
  padding: 10px 12px; margin-top: 10px; display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap;
}
.warning p { margin: 0; flex: 1 1 260px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.switch { display: inline-flex; gap: 8px; align-items: center; cursor: pointer; }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 16px; }

.history { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; align-items: start; }
.run-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.run-row {
  width: 100%; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 4px 8px; align-items: center;
  text-align: start; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; cursor: pointer;
}
.run-row[aria-current="true"] { border-color: var(--accent); background: var(--accent-soft); }
.run-counts { grid-column: 2 / -1; color: var(--muted); font-size: 13px; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
.dot.state-done { background: var(--ok); }
.dot.state-cancelled { background: var(--amber); }
.dot.state-failed { background: var(--danger); }
.dot.state-running { background: var(--accent); }
.run-details { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; }
.run-details h2 { font-size: 16px; margin: 0 0 8px; }
.run-details ul { list-style: none; margin: 0; padding: 0; }
.query-row { display: flex; justify-content: space-between; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--border); overflow-wrap: anywhere; }
.query-row:last-child { border-bottom: 0; }
.outcome-done { color: var(--ok); }
.outcome-failed { color: var(--danger); }
.outcome-skipped { color: var(--warn-text); }

.settings { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); }
.setting {
  display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; justify-content: space-between;
  padding: 12px 14px; border-bottom: 1px solid var(--border);
}
.setting:last-child { border-bottom: 0; }
.setting-value { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ok { color: var(--ok); }
.warn-text { color: var(--warn-text); }
.danger-zone > span { color: var(--danger); }

.overlay { position: fixed; inset: 0; background: rgb(2 6 23 / 55%); display: grid; place-items: center; padding: 16px; z-index: 20; }
.dialog { background: var(--surface); border-radius: 12px; padding: 18px; width: min(460px, 100%); border: 1px solid var(--border); }
.dialog h2 { margin: 0 0 8px; font-size: 18px; }
.dialog input { width: 100%; margin-top: 6px; }

.toast {
  position: fixed; inset-block-end: 16px; inset-inline-start: 16px; max-width: min(520px, calc(100vw - 32px));
  display: flex; gap: 10px; align-items: center; background: var(--surface); border: 1px solid var(--border);
  border-inline-start: 4px solid var(--accent); border-radius: 10px; padding: 10px 12px;
  box-shadow: 0 8px 24px rgb(15 23 42 / 18%); z-index: 30;
}
.toast-success { border-inline-start-color: var(--ok); }
.toast-warn { border-inline-start-color: var(--amber); }
.toast-error { border-inline-start-color: var(--danger); }
.toast-text { flex: 1; }
.toast-close { border: 0; background: transparent; cursor: pointer; font-size: 18px; color: var(--muted); }

.wizard-head { margin-bottom: 14px; }
.steps { display: flex; gap: 6px; margin-top: 6px; }
.steps span { flex: 1; height: 5px; border-radius: 999px; background: var(--border); }
.steps span.on { background: var(--accent); }
.choice-row { display: flex; gap: 10px; flex-wrap: wrap; }
.choice {
  min-width: 140px; padding: 14px 18px; border: 2px solid var(--border); border-radius: 10px;
  background: var(--surface); cursor: pointer; font-size: 16px;
}
.choice[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); }
.callout { border-radius: 8px; padding: 10px 12px; margin: 10px 0; }
.callout p { margin: 0 0 8px; }
.callout-ok { background: var(--accent-soft); }
.callout-warn { background: var(--warn-bg); border: 1px solid var(--warn-border); color: var(--warn-text); }

@media (max-width: 720px) {
  .shell { grid-template-columns: minmax(0, 1fr); grid-template-areas: "top" "nav" "main"; }
  .sidenav { flex-direction: row; overflow-x: auto; border-inline-end: 0; border-bottom: 1px solid var(--border); padding: 6px 8px; }
  .nav-item { flex: 0 0 auto; }
  .main { padding: 14px 16px; }
  .history { grid-template-columns: minmax(0, 1fr); }
  .search-box { max-width: none; }
}
```

- [ ] **Step 3: Write `index.html`**

Replace `src/web/static/index.html` with:

```html
<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Hawkeye Jobs</title>
  <link rel="stylesheet" href="styles.css">
  <!-- Module first: it registers the "hawkeye" component on alpine:init before Alpine starts. -->
  <script type="module" src="app.js"></script>
  <script defer src="vendor/alpine.min.js"></script>
</head>
<body x-data="hawkeye">
  <div class="boot" x-show="!ready && !fatal"><p>Hawkeye Jobs</p></div>

  <div class="fatal" x-cloak x-show="fatal" role="alert">
    <div>
      <p>تعذّر تشغيل الواجهة. أغلق التطبيق، ثم افتحه من جديد.</p>
      <p lang="en" dir="ltr">The interface could not start. Close the app and open it again.</p>
    </div>
  </div>

  <div class="shell" x-cloak x-show="ready" :class="{ 'is-wizard': inWizard() }">
    <header class="topbar">
      <span class="brand">Hawkeye Jobs</span>
      <div x-show="!inWizard()">
        <button type="button" class="btn" x-show="!run" @click="startSearch()" x-text="t('run.start')"></button>
        <button type="button" class="btn-danger" x-show="run" :disabled="!!(run && run.stopping)" @click="stopSearch()"
                x-text="run && run.stopping ? t('run.stopping') : t('run.stop')"></button>
      </div>
    </header>

    <nav class="sidenav" x-show="!inWizard()" :aria-label="t('nav.label')">
      <template x-for="p in pages" :key="p">
        <button type="button" class="nav-item" :aria-current="page === p ? 'page' : null" @click="go(p)">
          <span x-text="t('nav.' + p)"></span>
          <span class="nav-count" x-show="p === 'jobs' && count('new') > 0" x-text="count('new')"></span>
          <span class="nav-dot" x-show="p === 'settings' && updateAvailable()" aria-hidden="true"></span>
        </button>
      </template>
    </nav>

    <main class="main">
      <!-- ── First-run wizard: header and steps 1, 2 and 4 (step 3 is the search form below) ── -->
      <div class="wizard-head" x-show="inWizard()">
        <p class="hint" x-text="t('wizard.step', { n: wizard.step, total: 4 })"></p>
        <div class="steps" aria-hidden="true">
          <template x-for="n in 4" :key="n"><span :class="{ on: n <= wizard.step }"></span></template>
        </div>
      </div>

      <section x-show="wizardStep(1)">
        <h1 class="page-title" x-text="t('wizard.welcome')"></h1>
        <p x-text="t('wizard.intro')"></p>
        <h2 x-text="t('wizard.language_title')"></h2>
        <div class="choice-row">
          <button type="button" class="choice" lang="ar" :aria-pressed="(lang === 'ar').toString()" @click="setLanguage('ar')">العربية</button>
          <button type="button" class="choice" lang="en" :aria-pressed="(lang === 'en').toString()" @click="setLanguage('en')">English</button>
        </div>
        <div class="actions">
          <button type="button" class="btn" @click="wizard.step = 2" x-text="t('action.next')"></button>
        </div>
      </section>

      <section x-show="wizardStep(2)">
        <h1 class="page-title" x-text="t('wizard.browser_title')"></h1>
        <p class="callout callout-ok" x-show="chromeFound()" x-text="t('wizard.chrome_ok')"></p>
        <div class="callout callout-warn" x-show="!chromeFound()">
          <p x-text="t('wizard.chrome_missing')"></p>
          <div class="row">
            <button type="button" class="btn-ghost" @click="openUrl(chromeUrl)" x-text="t('action.download_chrome')"></button>
            <button type="button" class="btn-ghost" @click="recheckChrome()" x-text="t('wizard.recheck')"></button>
          </div>
        </div>
        <div class="actions">
          <button type="button" class="btn-ghost" @click="wizard.step = 1" x-text="t('action.back')"></button>
          <button type="button" class="btn" @click="wizard.step = 3" x-text="t('action.next')"></button>
        </div>
      </section>

      <section x-show="wizardStep(4)">
        <h1 class="page-title" x-text="t('wizard.ready_title')"></h1>
        <p x-text="t('wizard.ready_body')"></p>
        <p class="hint" x-text="estimateText()"></p>
        <div class="actions">
          <button type="button" class="btn-ghost" @click="wizard.step = 3" x-text="t('action.back')"></button>
          <button type="button" class="btn" @click="finishWizard()" x-text="t('wizard.start')"></button>
        </div>
      </section>

      <!-- ── Jobs ── -->
      <section x-show="showPage('jobs')">
        <div class="banner" x-show="run" role="status" aria-live="polite">
          <div class="banner-row">
            <strong x-text="bannerTitle()"></strong>
            <span x-text="t('banner.new', { n: run ? run.new : 0 })"></span>
          </div>
          <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="progress()">
            <div class="progress-fill" :style="{ width: progress() + '%' }"></div>
          </div>
          <p class="hint" x-text="bannerNow()"></p>
        </div>

        <div class="toolbar">
          <div class="chips" role="tablist">
            <template x-for="f in filters" :key="f">
              <button type="button" class="chip" role="tab" :aria-selected="(jobs.filter === f).toString()" @click="setFilter(f)">
                <span x-text="t('jobs.filter.' + f)"></span>
                <span class="chip-count" x-show="f !== 'all'" x-text="count(f)"></span>
              </button>
            </template>
          </div>
          <input type="search" class="search-box" x-model="jobs.text" @input="onJobsText()"
                 :placeholder="t('jobs.search_placeholder')" :aria-label="t('jobs.search_placeholder')">
        </div>

        <ul class="job-list" :aria-label="t('jobs.list_label')">
          <template x-for="job in jobs.items" :key="job.id">
            <li class="job" :class="'is-' + job.status">
              <div class="job-head">
                <h3 class="job-title" x-text="job.title"></h3>
                <span class="badge" x-text="jobSourceLabel(job)"></span>
              </div>
              <p class="job-meta">
                <span x-text="job.description"></span>
                <span x-show="jobLocation(job)" x-text="' · ' + jobLocation(job)"></span>
              </p>
              <p class="job-age">
                <span x-text="ageText(job.first_seen_at)"></span>
                <span class="tag-muted" x-show="job.status === 'dismissed'" x-text="t('jobs.dismissed_badge')"></span>
              </p>
              <div class="job-actions">
                <button type="button" class="btn-small" @click="openUrl(job.url)" x-text="t('jobs.open')"></button>
                <button type="button" class="btn-ghost-small" :aria-pressed="(job.status === 'saved').toString()"
                        @click="setJobStatus(job, 'saved')" x-text="t('jobs.save')"></button>
                <button type="button" class="btn-ghost-small" :aria-pressed="(job.status === 'applied').toString()"
                        @click="setJobStatus(job, 'applied')" x-text="t('jobs.applied')"></button>
                <button type="button" class="btn-ghost-small" :aria-pressed="(job.status === 'dismissed').toString()"
                        @click="setJobStatus(job, 'dismissed')" x-text="t('jobs.dismiss')"></button>
              </div>
            </li>
          </template>
        </ul>
        <p class="empty" x-show="!jobs.loading && !jobs.items.length" x-text="emptyText()"></p>
        <button type="button" class="btn-ghost load-more" x-show="jobs.items.length < jobs.total"
                @click="loadMore()" x-text="t('jobs.load_more')"></button>
      </section>

      <!-- ── Search form: the Search page and wizard step 3 ── -->
      <section x-show="showSearchForm()">
        <h1 class="page-title" x-show="!inWizard()" x-text="t('nav.search')"></h1>

        <div class="panel">
          <h2 x-text="t('search.title_1')"></h2>
          <p class="hint" x-text="t('search.keywords_hint')"></p>
          <div class="tags">
            <template x-for="(keyword, i) in search.keywords" :key="keyword">
              <span class="tag">
                <span x-text="keyword"></span>
                <button type="button" class="tag-remove" @click="removeKeyword(i)"
                        :aria-label="t('action.remove_item', { item: keyword })">×</button>
              </span>
            </template>
            <input type="text" class="tag-input" maxlength="100" x-model="search.keywordInput"
                   @keydown.enter.prevent="commitKeyword()" @keydown="keywordKeydown($event)" @blur="commitKeyword()"
                   :placeholder="t('search.keywords_placeholder')" :aria-label="t('search.title_1')">
          </div>
          <p class="field-error" role="alert" x-show="search.errors.keywords" x-text="fieldError('keywords')"></p>
          <p class="tip" x-text="t('search.keywords_tip')"></p>
        </div>

        <div class="panel">
          <h2 x-text="t('search.title_2')"></h2>
          <p class="hint" x-text="t('search.locations_hint')"></p>
          <div class="tags">
            <template x-for="id in search.locations" :key="id">
              <span class="tag">
                <span x-text="locationLabel(id)"></span>
                <button type="button" class="tag-remove" @click="removeLocation(id)"
                        :aria-label="t('action.remove_item', { item: locationLabel(id) })">×</button>
              </span>
            </template>
            <div class="combo" @click.outside="search.countryOpen = false">
              <input type="text" class="tag-input" role="combobox" aria-controls="country-list"
                     :aria-expanded="search.countryOpen.toString()" x-model="search.countryQuery"
                     @focus="search.countryOpen = true" @input="search.countryOpen = true"
                     @keydown.escape="search.countryOpen = false" @keydown.enter.prevent="pickFirstCountry()"
                     :placeholder="t('search.locations_placeholder')" :aria-label="t('search.title_2')">
              <ul id="country-list" class="combo-list" role="listbox" x-show="search.countryOpen && countryMatches().length">
                <template x-for="c in countryMatches()" :key="c.id">
                  <li role="option"><button type="button" @click="addLocation(c.id)" x-text="c.label"></button></li>
                </template>
              </ul>
            </div>
          </div>
          <p class="field-error" role="alert" x-show="search.errors.locations" x-text="fieldError('locations')"></p>
        </div>

        <div class="panel">
          <h2 x-text="t('search.title_3')"></h2>
          <div class="sources">
            <template x-for="id in sourceOrder" :key="id">
              <label class="source" :class="{ 'is-disabled': sourceDisabled(id) }">
                <input type="checkbox" :checked="search.sources.includes(id)" :disabled="sourceDisabled(id)" @change="toggleSource(id)">
                <span>
                  <strong x-text="t('source.' + id)"></strong>
                  <small x-text="sourceDisabled(id) ? t('search.browser_needed') : t('source.' + id + '.hint')"></small>
                </span>
              </label>
            </template>
          </div>
          <p class="field-error" role="alert" x-show="search.errors.sources" x-text="fieldError('sources')"></p>
          <div class="warning" role="note">
            <p x-text="t('search.posts_warning')"></p>
            <button type="button" class="btn-ghost" @click="linkedinLogin()" x-text="t('linkedin.login')"></button>
          </div>
        </div>

        <div class="panel">
          <h2 x-text="t('search.title_4')"></h2>
          <div class="row">
            <label class="switch">
              <input type="checkbox" x-model="search.auto.enabled">
              <span x-text="t('search.auto_label')"></span>
            </label>
            <select x-model.number="search.auto.hours" :disabled="!search.auto.enabled" :aria-label="t('search.auto_label')">
              <template x-for="h in autoHours" :key="h">
                <option :value="h" :selected="h === search.auto.hours" x-text="t('hours.' + h)"></option>
              </template>
            </select>
            <span class="hint" x-text="t('search.auto_note')"></span>
          </div>
        </div>

        <div class="actions" x-show="!inWizard()">
          <button type="button" class="btn" :disabled="search.saving || !!run" @click="saveSearch(true)" x-text="t('search.save_and_run')"></button>
          <button type="button" class="btn-ghost" :disabled="search.saving" @click="saveSearch(false)" x-text="t('search.save_only')"></button>
          <span class="hint" x-text="estimateText()"></span>
        </div>
        <div class="actions" x-show="inWizard()">
          <button type="button" class="btn-ghost" @click="wizard.step = 2" x-text="t('action.back')"></button>
          <button type="button" class="btn" :disabled="search.saving" @click="wizardNextFromSearch()" x-text="t('action.next')"></button>
          <span class="hint" x-text="estimateText()"></span>
        </div>
      </section>

      <!-- ── History ── -->
      <section x-show="showPage('history')">
        <h1 class="page-title" x-text="t('nav.history')"></h1>
        <p class="empty" x-show="!history.loading && !history.runs.length" x-text="t('history.empty')"></p>
        <div class="history" x-show="history.runs.length">
          <ul class="run-list">
            <template x-for="r in history.runs" :key="r.id">
              <li>
                <button type="button" class="run-row" :aria-current="history.selectedId === r.id ? 'true' : null" @click="selectRun(r.id)">
                  <span class="dot" :class="'state-' + r.state" aria-hidden="true"></span>
                  <span x-text="formatDate(r.started_at) + ' · ' + t('history.trigger.' + r.trigger)"></span>
                  <span x-text="t('history.state.' + r.state)"></span>
                  <span class="run-counts" x-text="t('history.counts', { found: r.found_count, new: r.new_count })"></span>
                </button>
              </li>
            </template>
          </ul>
          <div class="run-details">
            <p class="hint" x-show="!history.details" x-text="t('history.select_hint')"></p>
            <template x-if="history.details">
              <div>
                <h2 x-text="t('history.details_title', { when: formatDate(history.details.started_at) })"></h2>
                <p class="hint" x-show="!history.details.queries.length" x-text="t('history.no_details')"></p>
                <ul>
                  <template x-for="q in history.details.queries" :key="q.idx">
                    <li class="query-row">
                      <span x-text="queryLabel(q)"></span>
                      <span :class="'outcome-' + q.status" x-text="queryOutcomeText(q)"></span>
                    </li>
                  </template>
                </ul>
              </div>
            </template>
          </div>
        </div>
      </section>

      <!-- ── Settings ── -->
      <section x-show="showPage('settings')">
        <h1 class="page-title" x-text="t('nav.settings')"></h1>
        <div class="settings">
          <div class="setting">
            <label for="set-lang" x-text="t('settings.language')"></label>
            <select id="set-lang" @change="setLanguage($event.target.value)">
              <option value="ar" :selected="lang === 'ar'">العربية</option>
              <option value="en" :selected="lang === 'en'">English</option>
            </select>
          </div>
          <div class="setting">
            <label for="set-theme" x-text="t('settings.theme')"></label>
            <select id="set-theme" @change="setTheme($event.target.value)">
              <template x-for="th in themes" :key="th">
                <option :value="th" :selected="th === theme" x-text="t('theme.' + th)"></option>
              </template>
            </select>
          </div>
          <div class="setting">
            <span x-text="t('settings.browser')"></span>
            <span class="setting-value">
              <span class="ok" x-show="chromeFound()" x-text="t('settings.chrome_found')"></span>
              <span class="warn-text" x-show="!chromeFound()" x-text="t('settings.chrome_missing')"></span>
              <button type="button" class="btn-ghost" x-show="!chromeFound()" @click="openUrl(chromeUrl)" x-text="t('action.download_chrome')"></button>
            </span>
          </div>
          <div class="setting">
            <span x-text="t('settings.linkedin')"></span>
            <span class="setting-value">
              <span class="hint" x-text="linkedinProfile() ? t('settings.linkedin_ready') : t('settings.linkedin_none')"></span>
              <button type="button" class="btn-ghost" @click="linkedinLogin()" x-text="t('linkedin.login')"></button>
            </span>
          </div>
          <div class="setting">
            <span x-text="t('settings.version', { version: version() })"></span>
            <span class="setting-value">
              <span class="hint" x-text="updateText()"></span>
              <button type="button" class="btn-ghost" :disabled="settingsPage.checking" @click="checkUpdate()" x-text="t('settings.check_updates')"></button>
              <button type="button" class="btn" x-show="updateAvailable()" @click="openUrl(releasesUrl)" x-text="t('settings.download_update')"></button>
            </span>
          </div>
          <div class="setting">
            <label class="switch">
              <input type="checkbox" :checked="!!appSettings.update_check" @change="setUpdateCheck($event.target.checked)">
              <span x-text="t('settings.update_check')"></span>
            </label>
          </div>
          <div class="setting">
            <span x-text="t('settings.problem')"></span>
            <button type="button" class="btn-ghost" @click="reportProblem()" x-text="t('action.report_problem')"></button>
          </div>
          <div class="setting">
            <span x-text="t('settings.data_folder')"></span>
            <button type="button" class="btn-ghost" @click="openDataFolder()" x-text="t('settings.open_folder')"></button>
          </div>
          <div class="setting danger-zone">
            <span x-text="t('settings.delete_all')"></span>
            <button type="button" class="btn-danger-ghost" @click="openDeleteDialog()" x-text="t('settings.delete')"></button>
          </div>
        </div>
      </section>
    </main>
  </div>

  <div class="overlay" x-cloak x-show="settingsPage.confirmOpen" @keydown.escape.window="closeDeleteDialog()">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title">
      <h2 id="delete-title" x-text="t('settings.delete_title')"></h2>
      <p x-text="t('settings.delete_body')"></p>
      <label for="delete-confirm" x-text="t('settings.delete_type', { word: t('settings.delete_word') })"></label>
      <input id="delete-confirm" type="text" autocomplete="off" x-ref="deleteConfirm" x-model="settingsPage.confirmText">
      <div class="actions">
        <button type="button" class="btn-danger" :disabled="!canDelete() || settingsPage.busy" @click="resetData()" x-text="t('settings.delete_confirm')"></button>
        <button type="button" class="btn-ghost" @click="closeDeleteDialog()" x-text="t('action.cancel')"></button>
      </div>
    </div>
  </div>

  <div class="toast" x-cloak x-show="toast" :class="toast ? 'toast-' + toast.kind : ''" role="status" aria-live="polite">
    <span class="toast-text" x-text="toast ? t(toast.key, toast.params) : ''"></span>
    <button type="button" class="btn-ghost-small" x-show="toast && toast.action" @click="runToastAction()"
            x-text="toast && toast.action ? t(toast.action.label) : ''"></button>
    <button type="button" class="toast-close" @click="toast = null" :aria-label="t('action.close')">×</button>
  </div>
</body>
</html>
```

- [ ] **Step 4: Write `app.js`**

Create `src/web/static/app.js`:

```js
/*
 * app.js — The Hawkeye Jobs window: one Alpine.js component, "hawkeye".
 * Pure helpers live in logic.js and server calls in api.js. Job fields are
 * untrusted scraped text: they are only ever rendered with x-text.
 */
import * as L from "./logic.js";
import { ApiError, createApi, takeToken } from "./api.js";

const PAGE_SIZE = 50;
const TOAST_MS = 8000;
const CHROME_URL = "https://www.google.com/chrome/";
const RELEASES_URL = "https://github.com/Abdallahabusnineh/hawkeye-jobs/releases/latest";

const api = createApi(takeToken());

async function loadDictionaries() {
  const load = async (lang) => {
    const response = await fetch(`i18n/${lang}.json`, { cache: "no-store" });
    if (!response.ok) throw new Error(`i18n ${lang}: HTTP ${response.status}`);
    return response.json();
  };
  const [ar, en] = await Promise.all([load("ar"), load("en")]);
  return { ar, en };
}

function hawkeye() {
  return {
    ready: false,
    fatal: false,
    dicts: { ar: {}, en: {} },
    lang: "ar",
    theme: "auto",
    page: "jobs",
    pages: ["jobs", "search", "history", "settings"],
    filters: L.FILTERS,
    sourceOrder: L.SOURCE_ORDER,
    autoHours: L.AUTO_HOURS,
    themes: ["auto", "light", "dark"],
    chromeUrl: CHROME_URL,
    releasesUrl: RELEASES_URL,
    state: null,
    appSettings: {},
    run: null,
    now: Date.now(),
    toast: null,
    countries: [],
    countryLabels: {},
    jobs: { filter: "new", text: "", items: [], total: 0, loading: false },
    search: {
      keywords: [], locations: [], sources: [], keywordInput: "", countryQuery: "", countryOpen: false,
      auto: { enabled: true, hours: 6 }, errors: {}, estimate: null, saving: false,
    },
    history: { runs: [], selectedId: null, details: null, loading: false, loaded: false },
    settingsPage: { update: null, checking: false, confirmOpen: false, confirmText: "", busy: false },
    wizard: { step: 1 },
    timers: { refresh: null, text: null, estimate: null, toast: null },
    jobsRequest: 0,

    async init() {
      try {
        this.dicts = await loadDictionaries();
        await this.refreshState();
        this.lang = this.state.language;
        this.theme = this.state.theme;
        this.applyLanguage();
        this.applyTheme();
        await Promise.all([this.loadCountries(), this.loadSearchSettings(), this.loadJobs()]);
        this.connectEvents();
        setInterval(() => { this.now = Date.now(); }, 60000);
        this.ready = true;
      } catch (error) {
        console.error("Hawkeye Jobs could not start", error);
        this.fatal = true;
      }
    },

    // ── text ─────────────────────────────────────────────────────────
    t(key, params) { return L.translate(this.dicts[this.lang], this.dicts.en, key, params); },
    tn(base, n, params = {}) { return this.t(L.pluralKey(this.dicts[this.lang], base, n, this.lang), { n, ...params }); },
    has(key) { return Object.prototype.hasOwnProperty.call(this.dicts[this.lang] || {}, key); },
    applyLanguage() {
      document.documentElement.lang = this.lang;
      document.documentElement.dir = this.lang === "ar" ? "rtl" : "ltr";
    },
    applyTheme() {
      if (this.theme === "auto") delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = this.theme;
    },
    formatDate(iso) {
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return "";
      const locale = this.lang === "ar" ? "ar-u-nu-latn" : "en";
      return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
    },
    ageText(iso) {
      const parts = L.ageParts(iso, this.now);
      if (!parts) return "";
      const age = parts.base === "age.now" ? this.t("age.now") : this.tn(parts.base, parts.n);
      return this.t("jobs.found_age", { age });
    },
    sourceName(id) { return this.t(`source.${id}`); },
    sourceShort(id) { return this.t(`source.${id}.short`); },
    jobSourceLabel(job) {
      const id = L.jobSourceId(job.source);
      return id ? this.sourceShort(id) : job.source;
    },
    locationLabel(id) { return id ? (this.countryLabels[id] || id) : ""; },
    jobLocation(job) { return job.location_id ? this.locationLabel(job.location_id) : (job.location || ""); },

    // ── state helpers (safe before /api/state has loaded) ────────────
    inWizard() { return !!this.state && !this.state.onboarding_done; },
    wizardStep(n) { return this.ready && this.inWizard() && this.wizard.step === n; },
    showPage(name) { return this.ready && !this.inWizard() && this.page === name; },
    showSearchForm() { return this.showPage("search") || this.wizardStep(3); },
    chromeFound() { return !!(this.state && this.state.chrome && this.state.chrome.found); },
    linkedinProfile() { return !!(this.state && this.state.linkedin_profile); },
    version() { return this.state ? this.state.version : ""; },
    updateAvailable() { return !!(this.state && this.state.update && this.state.update.available); },
    count(status) { return (this.state && this.state.job_counts && this.state.job_counts[status]) || 0; },
    go(page) {
      this.page = page;
      if (page === "history") this.loadHistory();
      if (page === "jobs") this.reloadJobs();
    },

    // ── loading ──────────────────────────────────────────────────────
    async refreshState() {
      this.state = await api.get("/api/state");
      this.run = L.fromSnapshot(this.state.run, this.run);
    },
    async loadCountries() {
      this.countries = await api.get(`/api/countries?lang=${this.lang}`);
      this.countryLabels = Object.fromEntries(this.countries.map((c) => [c.id, c.label]));
    },
    async loadSearchSettings() {
      const [saved, app] = await Promise.all([api.get("/api/settings"), api.get("/api/app-settings")]);
      this.appSettings = app;
      this.search.keywords = [...saved.keywords];
      this.search.locations = [...saved.locations];
      this.search.sources = saved.sources.length ? L.orderSources(saved.sources) : L.defaultSources(this.chromeFound());
      this.search.auto = { enabled: !!app.auto_search.enabled, hours: Number(app.auto_search.hours) };
      this.requestEstimate();
    },

    // ── live events ──────────────────────────────────────────────────
    connectEvents() {
      const source = api.events();
      // (Re)connected: the server may have dropped us while we were slow or asleep. Resync.
      source.onopen = () => { this.refreshState().catch(() => {}); };
      source.onmessage = (message) => {
        let event;
        try {
          event = JSON.parse(message.data);
        } catch (error) {
          return;
        }
        this.handleEvent(event);
      };
    },
    handleEvent(event) {
      this.run = L.applyEvent(this.run, event);
      switch (event.type) {
        case "query_done":
          if (event.new > 0) this.scheduleRefresh();
          break;
        case "posts_skipped":
          this.notify("info", "notice.posts_skipped");
          break;
        case "login_required":
          this.notify("warn", "notice.login_required", {}, { label: "linkedin.login", run: () => this.linkedinLogin() });
          break;
        case "browser_failed":
          this.notify("warn", L.browserNoticeKey(event.reason), {}, event.reason === "chrome_not_found"
            ? { label: "action.download_chrome", run: () => this.openUrl(CHROME_URL) }
            : { label: "action.report_problem", run: () => this.reportProblem() });
          break;
        case "run_finished":
          this.scheduleRefresh(0);
          if (this.history.loaded) this.loadHistory();
          this.notify(event.state === "done" ? "success" : "info", L.runFinishedKey(event.state),
                      { found: event.found, new: event.new });
          break;
        case "run_error":
          this.scheduleRefresh(0);
          this.notify("error", "error.unexpected", {}, { label: "action.report_problem", run: () => this.reportProblem() });
          break;
        case "jobs_reported":
          this.scheduleRefresh(0);
          break;
        default:
          break;
      }
    },
    scheduleRefresh(delay = 1500) {
      if (this.timers.refresh) return;
      this.timers.refresh = setTimeout(async () => {
        this.timers.refresh = null;
        try {
          await this.refreshState();
        } catch (error) {
          return; // the next event retries
        }
        if (this.showPage("jobs")) this.reloadJobs();
      }, delay);
    },

    // ── messages ─────────────────────────────────────────────────────
    notify(kind, key, params = {}, action = null) {
      clearTimeout(this.timers.toast);
      this.toast = { kind, key, params, action };
      this.timers.toast = setTimeout(() => { this.toast = null; }, action ? TOAST_MS * 2 : TOAST_MS);
    },
    runToastAction() {
      const action = this.toast && this.toast.action;
      this.toast = null;
      if (action) action.run();
    },
    showError(error) {
      const key = error instanceof ApiError ? L.apiErrorKey(error.status, error.body) : "error.unexpected";
      let action = null;
      if (key === "error.chrome_not_found") action = { label: "action.download_chrome", run: () => this.openUrl(CHROME_URL) };
      else if (key === "error.unexpected") action = { label: "action.report_problem", run: () => this.reportProblem() };
      this.notify("error", key, {}, action);
    },

    // ── jobs ─────────────────────────────────────────────────────────
    async loadJobs(append = false) {
      const offset = append ? this.jobs.items.length : 0;
      const limit = append ? PAGE_SIZE : Math.min(200, Math.max(PAGE_SIZE, this.jobs.items.length));
      const request = ++this.jobsRequest;
      this.jobs.loading = true;
      try {
        const data = await api.get(L.jobsQuery({ filter: this.jobs.filter, text: this.jobs.text, offset, limit }));
        if (request !== this.jobsRequest) return;
        this.jobs.items = append ? [...this.jobs.items, ...data.jobs] : data.jobs;
        this.jobs.total = data.total;
      } finally {
        if (request === this.jobsRequest) this.jobs.loading = false;
      }
    },
    reloadJobs() { this.loadJobs().catch((error) => this.showError(error)); },
    loadMore() { this.loadJobs(true).catch((error) => this.showError(error)); },
    setFilter(filter) {
      this.jobs.filter = filter;
      this.jobs.items = [];
      this.reloadJobs();
    },
    onJobsText() {
      clearTimeout(this.timers.text);
      this.timers.text = setTimeout(() => {
        this.jobs.items = [];
        this.reloadJobs();
      }, 300);
    },
    emptyText() {
      return this.jobs.text.trim() ? this.t("jobs.empty.text") : this.t(`jobs.empty.${this.jobs.filter}`);
    },
    async setJobStatus(job, pressed) {
      const previous = job.status;
      const status = L.nextStatus(previous, pressed);
      job.status = status;
      try {
        await api.patch(`/api/jobs/${encodeURIComponent(job.id)}`, { status });
      } catch (error) {
        job.status = previous;
        this.showError(error);
        return;
      }
      this.refreshState().catch(() => {});
      if (status === "dismissed") {
        this.notify("info", "jobs.dismissed_toast", {}, {
          label: "action.undo",
          run: async () => {
            await this.setJobStatus(job, previous);
            this.reloadJobs();
          },
        });
      }
      if (!L.jobVisible(status, this.jobs.filter)) {
        setTimeout(() => {
          const before = this.jobs.items.length;
          this.jobs.items = this.jobs.items.filter((item) => item.id !== job.id);
          if (this.jobs.items.length < before) this.jobs.total = Math.max(0, this.jobs.total - 1);
        }, 300);
      }
    },

    // ── running a search ─────────────────────────────────────────────
    async startSearch() {
      if (this.run) return;
      try {
        await api.post("/api/runs");
      } catch (error) {
        if (error instanceof ApiError && error.body.error === "no_settings") {
          this.page = "search";
          this.notify("warn", "error.no_settings");
          return;
        }
        this.showError(error);
        return;
      }
      if (!this.run) this.run = L.startingRun();
      this.go("jobs");
    },
    async stopSearch() {
      if (!this.run) return;
      this.run = { ...this.run, stopping: true };
      try {
        await api.post("/api/runs/current/cancel");
      } catch (error) {
        if (error instanceof ApiError && error.body.error === "not_running") {
          this.run = null;
          return;
        }
        if (this.run) this.run = { ...this.run, stopping: false };
        this.showError(error);
      }
    },
    bannerTitle() {
      if (!this.run) return "";
      if (this.run.stopping) return this.t("run.stopping");
      if (this.run.run_id === null || !this.run.total) return this.t("banner.starting");
      return this.t("banner.progress", { done: this.run.done, total: this.run.total });
    },
    bannerNow() {
      const current = this.run && this.run.current;
      if (!current) return "";
      const params = { source: this.sourceShort(current.source), keyword: current.keyword,
                       location: this.locationLabel(current.location_id) };
      return this.t(current.location_id ? "banner.now" : "banner.now_nolocation", params);
    },
    progress() { return L.progressPercent(this.run); },

    // ── search form ──────────────────────────────────────────────────
    commitKeyword() {
      const result = L.addKeyword(this.search.keywords, this.search.keywordInput);
      this.search.keywords = result.list;
      this.setFieldError("keywords", result.error);
      if (!result.error) this.search.keywordInput = "";
      this.requestEstimate();
    },
    keywordKeydown(event) {
      if (event.key === "," || event.key === "،") {
        event.preventDefault();
        this.commitKeyword();
      } else if (event.key === "Backspace" && !this.search.keywordInput && this.search.keywords.length) {
        this.removeKeyword(this.search.keywords.length - 1);
      }
    },
    removeKeyword(index) {
      this.search.keywords = this.search.keywords.filter((_, i) => i !== index);
      this.setFieldError("keywords", null);
      this.requestEstimate();
    },
    countryMatches() { return L.filterCountries(this.countries, this.search.countryQuery, this.search.locations); },
    addLocation(id) {
      if (!this.search.locations.includes(id)) this.search.locations = [...this.search.locations, id];
      this.search.countryQuery = "";
      this.search.countryOpen = false;
      this.setFieldError("locations", null);
      this.requestEstimate();
    },
    pickFirstCountry() {
      const first = this.countryMatches()[0];
      if (first) this.addLocation(first.id);
    },
    removeLocation(id) {
      this.search.locations = this.search.locations.filter((item) => item !== id);
      this.requestEstimate();
    },
    sourceDisabled(id) { return !this.chromeFound() && L.BROWSER_SOURCES.includes(id); },
    toggleSource(id) {
      const has = this.search.sources.includes(id);
      this.search.sources = has ? this.search.sources.filter((item) => item !== id) : L.orderSources([...this.search.sources, id]);
      this.setFieldError("sources", null);
      this.requestEstimate();
    },
    setFieldError(field, code) { this.search.errors = { ...this.search.errors, [field]: code || null }; },
    fieldError(field) {
      const code = this.search.errors[field];
      if (!code) return "";
      const key = `errors.${field}.${code}`;
      return this.has(key) ? this.t(key) : this.t("errors.field.invalid");
    },
    searchPayload() {
      return { keywords: this.search.keywords, locations: this.search.locations, sources: this.search.sources };
    },
    requestEstimate() {
      clearTimeout(this.timers.estimate);
      this.timers.estimate = setTimeout(async () => {
        try {
          const data = await api.post("/api/estimate", this.searchPayload());
          this.search.estimate = data.queries ? data.seconds : null;
        } catch (error) {
          this.search.estimate = null;
        }
      }, 400);
    },
    estimateText() {
      if (this.search.estimate === null) return "";
      return this.tn("estimate.minutes", L.estimateMinutes(this.search.estimate));
    },
    async saveSearch(thenRun = false) {
      if (this.search.keywordInput.trim()) this.commitKeyword();
      this.search.saving = true;
      try {
        const cleaned = await api.put("/api/settings", this.searchPayload());
        this.search.keywords = cleaned.keywords;
        this.search.locations = cleaned.locations;
        this.search.sources = L.orderSources(cleaned.sources);
        this.search.errors = {};
        this.appSettings = await api.patch("/api/app-settings", {
          auto_search: { enabled: !!this.search.auto.enabled, hours: Number(this.search.auto.hours) },
        });
        await this.refreshState();
      } catch (error) {
        if (error instanceof ApiError && error.status === 422 && error.body.errors) this.search.errors = error.body.errors;
        else this.showError(error);
        return false;
      } finally {
        this.search.saving = false;
      }
      if (thenRun) await this.startSearch();
      else if (!this.inWizard()) this.notify("success", "search.saved");
      return true;
    },

    // ── history ──────────────────────────────────────────────────────
    async loadHistory() {
      this.history.loading = true;
      try {
        const data = await api.get("/api/runs?limit=50");
        this.history.runs = data.runs;
        this.history.loaded = true;
      } catch (error) {
        this.showError(error);
      } finally {
        this.history.loading = false;
      }
    },
    async selectRun(id) {
      this.history.selectedId = id;
      this.history.details = null;
      try {
        this.history.details = await api.get(`/api/runs/${id}`);
      } catch (error) {
        this.showError(error);
      }
    },
    queryLabel(q) {
      const params = { source: this.sourceName(q.source), keyword: q.keyword, location: this.locationLabel(q.location_id) };
      return this.t(q.location_id ? "history.query_location" : "history.query", params);
    },
    queryOutcomeText(q) {
      const outcome = L.queryOutcome(q);
      return this.t(outcome.key, outcome.params);
    },

    // ── settings ─────────────────────────────────────────────────────
    async patchApp(patch) {
      try {
        this.appSettings = await api.patch("/api/app-settings", patch);
        return true;
      } catch (error) {
        this.showError(error);
        return false;
      }
    },
    async setLanguage(lang) {
      if (!(await this.patchApp({ language: lang }))) return;
      this.lang = lang;
      this.applyLanguage();
      try {
        await this.loadCountries();
      } catch (error) {
        this.showError(error);
      }
    },
    async setTheme(theme) {
      if (await this.patchApp({ theme })) {
        this.theme = theme;
        this.applyTheme();
      }
    },
    async setUpdateCheck(on) {
      if (await this.patchApp({ update_check: on })) this.settingsPage.update = null;
    },
    async checkUpdate() {
      this.settingsPage.checking = true;
      try {
        this.settingsPage.update = await api.get("/api/update");
        await this.refreshState();
      } catch (error) {
        this.showError(error);
      } finally {
        this.settingsPage.checking = false;
      }
    },
    updateText() {
      const info = this.settingsPage.update;
      if (!info) return this.updateAvailable() ? this.t("settings.update_available", { version: this.state.update.latest }) : "";
      if (info.disabled) return this.t("settings.update_disabled");
      if (info.available) return this.t("settings.update_available", { version: info.latest });
      return info.latest ? this.t("settings.up_to_date") : this.t("settings.update_unknown");
    },
    async openUrl(url) {
      try {
        await api.post("/api/open-url", { url });
      } catch (error) {
        this.showError(error);
      }
    },
    async openDataFolder() {
      try {
        await api.post("/api/open-data-folder");
      } catch (error) {
        this.showError(error);
      }
    },
    async reportProblem() {
      try {
        await api.post("/api/report-problem"); // the backend opens the issue page itself
        this.notify("info", "settings.report_opened");
      } catch (error) {
        this.notify("error", "error.report_failed");
      }
    },
    async linkedinLogin() {
      try {
        await api.post("/api/linkedin/login");
        this.notify("info", "linkedin.opened");
      } catch (error) {
        this.showError(error);
      }
    },
    openDeleteDialog() {
      this.settingsPage.confirmText = "";
      this.settingsPage.confirmOpen = true;
      this.$nextTick(() => { if (this.$refs.deleteConfirm) this.$refs.deleteConfirm.focus(); });
    },
    closeDeleteDialog() { this.settingsPage.confirmOpen = false; },
    canDelete() { return this.settingsPage.confirmText.trim() === this.t("settings.delete_word"); },
    async resetData() {
      if (!this.canDelete()) return;
      this.settingsPage.busy = true;
      try {
        await api.post("/api/reset", { confirm: "DELETE" });
      } catch (error) {
        this.showError(error);
        return;
      } finally {
        this.settingsPage.busy = false;
      }
      this.settingsPage.confirmOpen = false;
      this.history = { runs: [], selectedId: null, details: null, loading: false, loaded: false };
      this.jobs.items = [];
      await this.refreshState().catch(() => {});
      this.reloadJobs();
      this.notify("success", "settings.deleted");
    },

    // ── first-run wizard ─────────────────────────────────────────────
    async recheckChrome() {
      try {
        await this.refreshState();
        if (!this.search.sources.some((id) => L.BROWSER_SOURCES.includes(id)) && this.chromeFound()) {
          this.search.sources = L.defaultSources(true);
          this.requestEstimate();
        }
      } catch (error) {
        this.showError(error);
      }
    },
    async wizardNextFromSearch() {
      if (await this.saveSearch(false)) this.wizard.step = 4;
    },
    async finishWizard() {
      if (!(await this.patchApp({ onboarding_done: true }))) return;
      await this.refreshState().catch(() => {});
      if (this.state) this.state.onboarding_done = true;
      this.page = "jobs";
      await this.startSearch();
    },
  };
}

document.addEventListener("alpine:init", () => {
  window.Alpine.data("hawkeye", hawkeye);
});
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
- `venv/bin/python3 -m unittest tests.test_frontend tests.test_api -v 2>&1 | tail -6`. Expected: `OK`. The guards now scan `index.html` and `app.js`: every literal key exists and no HTML sinks are used.
- `node --check src/web/static/app.js && echo syntax-ok`. Expected: `syntax-ok`.

- [ ] **Step 6: Smoke-check in a scratch data dir (no window, no search)**

```bash
SCRATCH=$(mktemp -d)
HAWKEYE_DATA_DIR="$SCRATCH" GMAIL_USER= GMAIL_PASS= venv/bin/python3 src/app.py --no-window --port 8765 > "$SCRATCH/out.txt" 2>&1 &
APP_PID=$!
sleep 3
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/app.js
curl -s -I http://127.0.0.1:8765/ | grep -i -E "content-security-policy|referrer-policy"
kill -INT $APP_PID; wait $APP_PID
cat "$SCRATCH/out.txt"
```

Expected:
- `200`, `200`;
- both security headers printed;
- the app prints its URL and exits without a traceback.

Do not start a search here.

- [ ] **Step 7: Full suite and commit**

Run: `venv/bin/python3 -m unittest discover -s tests 2>&1 | tail -3`. Expected: `OK`.

```bash
git add src/web/static/index.html src/web/static/styles.css src/web/static/app.js tests/test_api.py
git commit -m "Add the Hawkeye Jobs window: jobs with live progress, search, history, settings, first-run wizard"
```

---

### Task 5: Docs

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: Tasks 1–4.

- [ ] **Step 1: README**

In the "Desktop app (preview)" section of `README.md`, replace the sentence that says the interface is still being built with:

```markdown
The window has four pages — **Jobs** (with live progress while a search runs), **Search**, **History** and **Settings** — and a short first-run wizard. Arabic (right-to-left) and English. Everything runs on your computer: the window talks to a small server on 127.0.0.1 that only this app can use. Downloadable installers come later.

For UI development, serve without a window on a fixed port and open the printed URL in a browser:

    HAWKEYE_DATA_DIR=/tmp/hawkeye-dev venv/bin/python3 src/app.py --no-window --port 8765
```

- [ ] **Step 2: CLAUDE.md**

Add these rows to the "Key files to know" table, after the `src/web/redact.py` row:

```markdown
| `src/web/static/index.html` | The window's markup (Alpine.js `x-*` attributes; job text only via `x-text`) |
| `src/web/static/app.js` | The `hawkeye` Alpine component: pages, wizard, SSE handling |
| `src/web/static/logic.js` | Pure UI helpers (tested with `node --test tests/js/`) |
| `src/web/static/api.js` | Local API client; moves the token from the URL to sessionStorage |
| `src/web/static/i18n/*.json` | All UI text, formal Arabic and English (identical keys; `tests/test_frontend.py`) |
```

Change the "Tests:" line to:

```markdown
Tests: `venv/bin/python3 -m unittest discover -s tests -v` (also runs `node --test tests/js/*.test.mjs` when node is installed)
```

- [ ] **Step 3: ARCHITECTURE.md**

Add the following subsection at the end of the "Desktop app" section, before the endpoint table. Also add a row for `POST /estimate` to the endpoint table: `| POST | /estimate | 200 {queries, seconds} (never fails; zeros for missing input) | |`.

```markdown
### Front-end (`src/web/static/`)

No build step. `index.html` loads `app.js` (an ES module that registers the Alpine component `hawkeye` on `alpine:init`) and then the vendored Alpine.js 3.14.9 (`vendor/`, MIT licence, SHA-256 in `vendor/SHA256SUMS`, checked by `tests/test_frontend.py`).

- **Token:** `api.js` `takeToken()` reads `?token=` once, stores it in `sessionStorage` and removes it from the address bar, so a reload keeps working and the token never appears in a Referer. Every call sends `X-Hawkeye-Token`; only `EventSource` uses `/api/events?token=`.
- **Text:** every displayed word comes from `i18n/ar.json` / `i18n/en.json` (identical keys and placeholders; formal Arabic, checked for colloquial words). Plurals use `Intl.PluralRules` with `.one/.two/.few/.many/.other` keys. API error codes map to `error.*` keys in `logic.js` `apiErrorKey()`.
- **Live progress:** `handleEvent()` folds SSE events into `run` with `logic.applyEvent()`; on every (re)connect `onopen` re-reads `/api/state` (`fromSnapshot`), so a dropped subscriber resyncs. New jobs refresh the list at most every 1.5 s.
- **Safety:** scraped job fields are rendered only with `x-text`; `tests/test_frontend.py` fails on `x-html`/`innerHTML`. Links open through `POST /api/open-url`. The server adds a Content-Security-Policy (`script-src 'self' 'unsafe-eval'` for Alpine, nothing from other origins), `Referrer-Policy: no-referrer` and `Cache-Control: no-store` to every response.
- **RTL:** `<html dir>` follows the language; CSS uses logical properties only.
- **Wizard:** shown while `onboarding_done` is false: language → Chrome check → the Search form → first search (`finishWizard()` sets `onboarding_done`, which also enables auto-search).
```

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md docs/ARCHITECTURE.md
git commit -m "Document the desktop window front-end"
```

---

### Task 6: Browser check (controller, built-in browser) and real end-to-end

This task is run by the controller, not a subagent. It needs the built-in browser and judgement on what it sees. Findings become one fix dispatch.

**Files:**
- No new files. Fixes go to the Task 4 files; record what was checked in the final report.

- [ ] **Step 1: Seed a scratch data dir and serve**

```bash
SCRATCH=$(mktemp -d /tmp/hawkeye-ui.XXXX)
HAWKEYE_DATA_DIR="$SCRATCH" venv/bin/python3 - <<'EOF'
import sys
sys.path.insert(0, "src")
from store import Store
with Store() as store:
    run = store.start_run({"keywords": ["Java"], "locations": ["jo"], "sources": ["linkedin", "bayt"]}, trigger="auto")
    store.add_jobs(run, [
        {"title": "Senior Java Developer", "url": "https://example.com/1", "description": "Acme - Amman",
         "source": "Linkedin", "location": "Jordan", "location_id": "jo", "published": ""},
        {"title": "<img src=x onerror=alert(1)> Backend Engineer", "url": "https://example.com/2",
         "description": "<b>Hostile Co</b>", "source": "Bayt", "location": "Jordan", "location_id": "jo", "published": ""},
        {"title": "Java Spring Boot Engineer", "url": "https://example.com/3", "description": "Gulf Co - Riyadh",
         "source": "Google Jobs", "location": "Saudi Arabia", "location_id": "sa", "published": ""},
    ])
    for i, (source, status, detail) in enumerate([("linkedin", "query_done", None), ("bayt", "query_failed", "HTTPError"),
                                                  ("linkedin_posts", "query_skipped", "login_required")]):
        event = {"type": status, "index": i, "source": source, "keyword": "Java",
                 "location_id": None if source == "linkedin_posts" else "jo", "found": 2 if i == 0 else 0, "new": 1 if i == 0 else 0}
        if status == "query_failed": event["error"] = detail
        if status == "query_skipped": event["reason"] = detail
        store.record_query(run, event)
    store.finish_run(run, "done", 3, 3)
EOF
HAWKEYE_DATA_DIR="$SCRATCH" GMAIL_USER= GMAIL_PASS= venv/bin/python3 src/app.py --no-window --port 8765
```

Run the last command in the background. Open the printed URL, including `?token=…`, in the built-in browser.

- [ ] **Step 2: Walk the checklist** (screenshots plus `read_console_messages` after each block; there must be no errors and no CSP violations)
  1. **Wizard (Arabic, RTL):**
     - the language step works, and switching to English flips to LTR and back to Arabic;
     - the Chrome step shows the right state;
     - on the search step:
       - saving with no keywords shows the Arabic field error;
       - then add "Java", choose الأردن, and tick **LinkedIn — Jobs only** (one polite query);
       - the estimate appears;
     - Next shows the ready step.
  2. **Real search, minimal and polite:**
     - press «ابدأ البحث الأول»;
     - the banner shows «جارٍ تجهيز البحث…», then progress, and the top button becomes «إيقاف البحث».
  3. **Reconnect:** while it runs, call `location.reload()` with `javascript_tool`. The page must load without `?token=` in the address bar (Review Focus 2) and the banner must come back with the same progress (Review Focus 4).
  4. **Finish:** let it finish (≈30 s). The toast shows the summary and the wizard does not appear again.
  5. **Hostile job text:** under All, the `<img …>` title and `<b>Hostile Co</b>` show as literal text, and no image or alert appears (Review Focus 1).
  6. **Jobs:**
     - chip counts;
     - Save → moves to Saved;
     - Not interested → hidden, with an Undo toast that restores it;
     - text search for "Spring";
     - "Found …" ages in Arabic.
  7. **History:**
     - the seeded run shows «تلقائي» and «اكتمل»;
     - its details show done / «تعذّر الوصول مؤقتًا» / «يلزم تسجيل الدخول»;
     - the real search shows «يدوي».
  8. **Settings:**
     - theme Dark, then Light, then Auto;
     - language English (check every page in LTR), then Arabic;
     - Check for updates shows one of the update texts;
     - the Delete dialog stays disabled until «حذف» is typed.
  9. **360 px:** `resize_window` to 360×740. There is no horizontal scroll and the nav becomes a top strip. Check every page. Then reset to desktop.
  10. **Delete:** delete all data with «حذف». The Jobs page becomes empty and the Search page still holds the settings.
- [ ] **Step 3: Fix wave**

Collect every defect into one list and dispatch one fix subagent (Task 4 files only), then re-check the affected steps. Stop the server with Ctrl+C (`kill -INT`).

- [ ] **Step 4: Real end-to-end in the native window** (spec §8.3)

```bash
SCRATCH=$(mktemp -d /tmp/hawkeye-e2e.XXXX)
HAWKEYE_DATA_DIR="$SCRATCH" GMAIL_USER= GMAIL_PASS= venv/bin/python3 src/app.py
```

Then:
1. Complete the wizard in the window: one keyword, one country, LinkedIn plus Google Jobs.
2. Watch live progress and the jobs arriving.
3. Note whether a desktop notification appears. An unbundled Python on macOS may not show one; this is a known Part 3 item.
4. **While it runs,** in another shell:

   ```bash
   printf 'y\n' | HAWKEYE_DATA_DIR="$SCRATCH" GMAIL_USER= GMAIL_PASS= venv/bin/python3 src/main.py
   ```

   It must print the "Another Hawkeye Jobs search is already running" line and exit 0.
5. Close the window. The process exits within ≤35 s. Known deviation from spec §5: the window closes at once and the app finishes stopping in the background, without showing «جارٍ الإيقاف…». Record it as a follow-up for Part 3 if it feels wrong in use.

Record the results for the final report.

---
