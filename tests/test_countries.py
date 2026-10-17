import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from countries import load_countries, localized_countries
from picker import SearchSelectState, apply_search_key


class LoadCountriesTests(unittest.TestCase):
    def test_offline_list_is_un_states_plus_three(self):
        with patch("requests.get", side_effect=AssertionError("no network")):
            countries = load_countries()
        ids = [c["id"] for c in countries]
        self.assertEqual(countries[0], {"id": "remote", "label": "Remote"})
        self.assertEqual(len(ids), 1 + 197)          # Remote + 193 UN states + Vatican, Palestine, HK, Macau
        self.assertEqual(len(ids), len(set(ids)))
        for cid in ("jo", "ae", "sa", "eg", "de", "us", "ps", "hk", "mo", "va"):
            self.assertIn(cid, ids)
        for not_listed in ("eh", "tw", "xk", "aq", "bv", "pr", "gl", "eu", "zz"):
            self.assertNotIn(not_listed, ids)

    def test_sorted_by_english_name(self):
        labels = [c["label"] for c in load_countries()[1:]]
        self.assertEqual(labels, sorted(labels, key=str.casefold))

    def test_every_row_has_both_names_in_the_right_script(self):
        import json
        import re
        import countries
        rows = json.loads(countries.DATA.read_text(encoding="utf-8"))
        for r in rows:
            self.assertRegex(r["id"], r"^[a-z]{2}$")
            self.assertTrue(r["en"] and r["ar"], r)
            self.assertIsNone(re.search(r"[\u0600-\u06FF]", r["en"]), r)
            self.assertRegex(r["ar"], r"[\u0621-\u064A]", r)
            # Persian/Urdu-only letters (ڤ ک ی پ چ گ) mean a non-Arabic spelling slipped in
            self.assertIsNone(re.search("[\u06A4\u06A9\u06CC\u067E\u0686\u06AF]", r["ar"]), r)

    def test_reviewed_name_fixes(self):
        ar = {c["id"]: c["label"] for c in localized_countries("ar", load_countries())}
        self.assertEqual(ar["mx"], "المكسيك")
        self.assertEqual(ar["rs"], "صربيا")
        self.assertEqual(ar["cd"], "جمهورية الكونغو الديمقراطية")
        self.assertEqual(ar["cg"], "جمهورية الكونغو")
        self.assertEqual(ar["ps"], "فلسطين")
        self.assertEqual(ar["sa"], "السعودية")

    def test_every_site_mapping_is_in_the_list(self):
        from catalog import OVERRIDES
        ids = {c["id"] for c in load_countries()}
        self.assertEqual(set(OVERRIDES) - ids, set())


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




class LocalizedCountriesTests(unittest.TestCase):
    BASE = [{"id": "remote", "label": "Remote"}, {"id": "jo", "label": "Jordan"},
            {"id": "sa", "label": "Saudi Arabia"}, {"id": "zz", "label": "Zedland"}]

    def test_arabic_labels_with_remote_first(self):
        result = localized_countries("ar", self.BASE)
        self.assertEqual(result[0], {"id": "remote", "label": "عن بُعد"})
        labels = {c["id"]: c["label"] for c in result}
        self.assertEqual(labels["jo"], "الأردن")
        self.assertEqual(labels["sa"], "السعودية")
        self.assertEqual(labels["zz"], "Zedland")  # not in the data: keep the base label

    def test_english_sorted(self):
        result = localized_countries("en", self.BASE)
        self.assertEqual([c["id"] for c in result], ["remote", "jo", "sa", "zz"])
        self.assertEqual(result[0]["label"], "Remote")


if __name__ == "__main__":
    unittest.main()
