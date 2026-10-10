import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from countries import CountryFetchError, load_countries, parse_countries
from picker import SearchSelectState, apply_search_key


SAMPLE_API = [
    {"name": {"common": "Jordan"}, "cca2": "JO"},
    {"name": {"common": "Germany"}, "cca2": "DE"},
    {"name": {"common": "United Arab Emirates"}, "cca2": "AE"},
]


class ParseCountriesTests(unittest.TestCase):
    def test_parses_and_sorts_by_name(self):
        countries = parse_countries(SAMPLE_API)
        self.assertEqual(
            [(c["id"], c["label"]) for c in countries],
            [
                ("de", "Germany"),
                ("jo", "Jordan"),
                ("ae", "United Arab Emirates"),
            ],
        )

    def test_parses_first_org_payload(self):
        payload = {
            "status": "ok",
            "data": {
                "DE": {"country": "Germany", "region": "Europe"},
                "JO": {"country": "Jordan", "region": "ME"},
            },
        }
        countries = parse_countries(payload)
        self.assertEqual(
            [(c["id"], c["label"]) for c in countries],
            [("de", "Germany"), ("jo", "Jordan")],
        )

    def test_strips_un_the_suffix(self):
        payload = {
            "data": {
                "AE": {"country": "United Arab Emirates (the)"},
            }
        }
        countries = parse_countries(payload)
        self.assertEqual(countries[0]["label"], "United Arab Emirates")

    def test_parses_countriesnow_payload(self):
        payload = {
            "error": False,
            "data": [
                {"name": "Jordan", "Iso2": "JO"},
                {"name": "Germany", "Iso2": "DE"},
            ],
        }
        countries = parse_countries(payload)
        self.assertEqual(
            [(c["id"], c["label"]) for c in countries],
            [("de", "Germany"), ("jo", "Jordan")],
        )


class LoadCountriesTests(unittest.TestCase):
    def test_uses_api_and_writes_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            countries = load_countries(
                fetcher=lambda url: SAMPLE_API,
                cache_path=cache,
            )
            ids = [c["id"] for c in countries]
            self.assertEqual(ids[0], "remote")
            self.assertIn("jo", ids)
            self.assertTrue(cache.exists())

    def test_calls_api_even_when_cache_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            cache.write_text(json.dumps([{"id": "jp", "label": "Japan"}]), encoding="utf-8")
            countries = load_countries(
                fetcher=lambda url: SAMPLE_API,
                cache_path=cache,
            )
            ids = [c["id"] for c in countries]
            self.assertIn("jo", ids)
            self.assertIn("de", ids)
            self.assertNotIn("jp", ids)

    def test_falls_back_to_cache_when_api_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            cache.write_text(json.dumps([{"id": "jp", "label": "Japan"}]), encoding="utf-8")

            def boom(url):
                raise RuntimeError("offline")

            countries = load_countries(fetcher=boom, cache_path=cache)
            self.assertEqual(countries[0]["id"], "remote")
            self.assertTrue(any(c["id"] == "jp" for c in countries))

    def test_raises_when_api_and_cache_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "missing.json"

            def boom(url):
                raise RuntimeError("offline")

            with patch("countries.LEGACY_CACHE", Path(tmp) / "no-legacy.json"), \
                 self.assertRaises(CountryFetchError):
                load_countries(fetcher=boom, cache_path=cache)

    def test_falls_back_to_legacy_repo_root_cache_without_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy.json"
            legacy.write_text(json.dumps([{"id": "jp", "label": "Japan"}]), encoding="utf-8")
            cache = Path(tmp) / "data" / "cache.json"

            def boom(url):
                raise RuntimeError("offline")

            with patch("countries.LEGACY_CACHE", legacy):
                countries = load_countries(fetcher=boom, cache_path=cache)
            self.assertTrue(any(c["id"] == "jp" for c in countries))
            self.assertFalse(cache.exists())


class SearchSelectTests(unittest.TestCase):
    def setUp(self):
        self.state = SearchSelectState(items=["Jordan", "Germany", "Japan"])

    def test_typing_filters_list(self):
        state = apply_search_key(self.state, "type:j")
        state = apply_search_key(state, "type:o")
        self.assertEqual(state.query, "jo")
        self.assertEqual(state.visible_indexes(), [0])

    def test_space_toggles_filtered_row(self):
        state = apply_search_key(self.state, "type:g")
        state = apply_search_key(state, "space")
        self.assertEqual(state.selected, [False, True, False])

    def test_letter_a_is_search_not_select_all(self):
        state = apply_search_key(self.state, "type:a")
        self.assertEqual(state.query, "a")
        self.assertFalse(any(state.selected))

    def test_star_selects_visible_matches(self):
        state = apply_search_key(self.state, "type:j")
        state = apply_search_key(state, "all")
        self.assertEqual(state.selected, [True, False, True])

    def test_enter_confirms_highlighted_when_none_selected(self):
        state = apply_search_key(self.state, "down")
        state = apply_search_key(state, "enter")
        self.assertTrue(state.done)
        self.assertEqual(state.indexes(), [1])

    def test_enter_keeps_already_selected_countries(self):
        state = apply_search_key(self.state, "space")
        state = apply_search_key(state, "down")
        state = apply_search_key(state, "enter")
        self.assertTrue(state.done)
        self.assertEqual(state.indexes(), [0])


class CountryCachePathTests(unittest.TestCase):
    def test_default_cache_lives_in_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HAWKEYE_DATA_DIR": tmp}):
                load_countries(fetcher=lambda url: SAMPLE_API)
            self.assertTrue((Path(tmp) / "countries_cache.json").exists())


if __name__ == "__main__":
    unittest.main()
