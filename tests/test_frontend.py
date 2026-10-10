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
              "لسّا", "بس", "هاد", "هاي", "تبعك", "اشي", "إشي", "يلا", "مو", "ايش", "إيش", "زي", "خلص",
              "وين", "ازاي", "كده", "دلوقتي", "هسا", "هلا", "بدنا", "ايه", "ليه", "شلون", "برضو", "كمان", "مين")
ARABIC_LETTER = "؀-ۿ"
ARABIC_DIACRITICS = re.compile("[ً-ْـ]")
ARABIC_DIGITS = re.compile("[٠-٩۰-۹]")
HTML_SINKS = ("x-html", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
              "setHTMLUnsafe", "createContextualFragment", "DOMParser", "srcdoc", "javascript:",
              "eval(", "new Function")
ALPINE_SHA256 = "3ed1eed252488921df65e363d6715deb04d7f92aaedb9e52199fdf73cb1e0ad3"
# The only outbound links the app may show. They are opened in the system browser through
# POST /api/open-url; the app never fetches them itself.
ALLOWED_LINKS = (
    "https://www.google.com/chrome/",
    "https://github.com/Abdallahabusnineh/hawkeye-jobs/releases/latest",
)


def _load(lang):
    return json.loads((STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def _code(name):
    path = STATIC / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _normalise_arabic(text):
    """Strip diacritics and tatweel, and unify alef forms and alef maqsura, so spelling variants match."""
    text = ARABIC_DIACRITICS.sub("", text)
    return text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"}))


def _front_end_files(suffixes):
    """Every file under src/web/static with one of the suffixes, except the vendored third-party code."""
    return sorted(p for p in STATIC.rglob("*")
                  if p.is_file() and p.suffix in suffixes and "vendor" not in p.relative_to(STATIC).parts)


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
            normalised = _normalise_arabic(value)
            for word in COLLOQUIAL:
                # An optional one-letter clitic prefix (و ف ب ل ك س) must not hide a colloquial word.
                pattern = (r"(?<![" + ARABIC_LETTER + r"])[وفبلكس]?" + re.escape(_normalise_arabic(word))
                           + r"(?![" + ARABIC_LETTER + r"])")
                self.assertIsNone(re.search(pattern, normalised), f"colloquial «{word}» in ar:{key}")

    def test_arabic_uses_latin_digits(self):
        for key, value in self.ar.items():
            self.assertIsNone(ARABIC_DIGITS.search(value), f"Arabic-Indic digit in ar:{key}")

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
        for path in _front_end_files({".js", ".html"}):
            text = path.read_text(encoding="utf-8")
            for sink in HTML_SINKS:
                self.assertNotIn(sink, text, f"{sink} in {path.relative_to(STATIC)}")

    def test_only_local_scripts_and_styles(self):
        html = _code("index.html")
        for match in re.finditer(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", html):
            target = match.group(1)
            self.assertFalse(re.match(r"^(?:[a-z]+:)?//", target, re.I), f"remote resource {target}")

        css = _code("styles.css")
        self.assertIsNone(re.search(r"@import[^;]*?(?:https?:)?//", css, re.I), "remote @import in styles.css")
        self.assertIsNone(re.search(r"url\(\s*[\"']?(?:https?:)?//", css, re.I), "remote url() in styles.css")

        for path in _front_end_files({".js"}):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"""(["'`])((?:https?://|//[^\s"'`/])[^"'`\s]*)\1""", text):
                literal = match.group(2)
                self.assertIn(literal, ALLOWED_LINKS, f"unexpected URL literal {literal} in {path.relative_to(STATIC)}")

    def test_vendored_alpine_matches_checksum(self):
        vendor = STATIC / "vendor"
        expected, name = (vendor / "SHA256SUMS").read_text().split()
        self.assertEqual(name, "alpine.min.js")
        digest = hashlib.sha256((vendor / name).read_bytes()).hexdigest()
        self.assertEqual(digest, ALPINE_SHA256)
        self.assertEqual(expected, ALPINE_SHA256)
        self.assertIn("3.14.9", (vendor / name).read_text(encoding="utf-8"))
        self.assertIn("MIT License", (vendor / "LICENSE.md").read_text(encoding="utf-8"))

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
