import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from countries import load_countries, localized_countries
from picker import SearchSelectState, apply_search_key


class LoadCountriesTests(unittest.TestCase):
    def test_offline_world_list_with_remote_first(self):
        with patch("requests.get", side_effect=AssertionError("no network")):
            countries = load_countries()
        ids = [c["id"] for c in countries]
        self.assertEqual(countries[0], {"id": "remote", "label": "Remote"})
        self.assertGreater(len(ids), 200)
        for cid in ("jo", "ae", "de", "us", "ps", "xk"):
            self.assertIn(cid, ids)
        for not_a_country in ("eu", "un", "zz", "xa", "ic"):
            self.assertNotIn(not_a_country, ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_sorted_by_english_name(self):
        labels = [c["label"] for c in load_countries()[1:]]
        self.assertEqual(labels, sorted(labels, key=str.casefold))

    def test_palestine_named_plainly(self):
        labels = {c["id"]: c["label"] for c in load_countries()}
        self.assertEqual(labels["ps"], "Palestine")
        self.assertEqual({c["id"]: c["label"] for c in localized_countries("ar", load_countries())}["ps"], "فلسطين")

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
        self.assertEqual(labels["sa"], "المملكة العربية السعودية")
        self.assertEqual(labels["zz"], "Zedland")  # unknown to CLDR: keep the base label

    def test_english_sorted(self):
        result = localized_countries("en", self.BASE)
        self.assertEqual([c["id"] for c in result], ["remote", "jo", "sa", "zz"])
        self.assertEqual(result[0]["label"], "Remote")


if __name__ == "__main__":
    unittest.main()
