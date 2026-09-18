import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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

            with self.assertRaises(CountryFetchError):
                load_countries(fetcher=boom, cache_path=cache)


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


if __name__ == "__main__":
    unittest.main()
