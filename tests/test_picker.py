import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from picker import (
    MultiSelectState,
    SearchSelectState,
    SingleSelectState,
    apply_multiselect_key,
    apply_singleselect_key,
    _multi_lines,
    _search_box_lines,
    _search_lines,
    _single_lines,
    GREEN,
    CYAN,
    MAGENTA,
)


class MultiSelectKeyTests(unittest.TestCase):
    def setUp(self):
        self.state = MultiSelectState(items=["A", "B", "C"])

    def test_space_toggles_current_row(self):
        state = apply_multiselect_key(self.state, "space")
        self.assertEqual(state.selected, [True, False, False])
        state = apply_multiselect_key(state, "space")
        self.assertEqual(state.selected, [False, False, False])

    def test_arrows_move_and_wrap(self):
        state = apply_multiselect_key(self.state, "up")
        self.assertEqual(state.cursor, 2)
        state = apply_multiselect_key(state, "down")
        self.assertEqual(state.cursor, 0)

    def test_all_selects_every_row(self):
        state = apply_multiselect_key(self.state, "all")
        self.assertEqual(state.selected, [True, True, True])

    def test_enter_without_selection_stays_open(self):
        state = apply_multiselect_key(self.state, "enter")
        self.assertFalse(state.done)
        self.assertTrue(state.error)

    def test_enter_with_selection_finishes(self):
        state = apply_multiselect_key(self.state, "space")
        state = apply_multiselect_key(state, "enter")
        self.assertTrue(state.done)
        self.assertEqual(state.indexes(), [0])


class SingleSelectKeyTests(unittest.TestCase):
    def test_enter_picks_highlighted(self):
        state = SingleSelectState(items=["Yes", "No"], cursor=1)
        state = apply_singleselect_key(state, "enter")
        self.assertTrue(state.done)
        self.assertEqual(state.choice, 1)

    def test_letter_shortcuts(self):
        state = SingleSelectState(items=["Yes", "No"])
        yes = apply_singleselect_key(state, "y")
        self.assertEqual(yes.choice, 0)
        self.assertTrue(yes.done)
        no = apply_singleselect_key(state, "n")
        self.assertEqual(no.choice, 1)
        self.assertTrue(no.done)


class ColorTests(unittest.TestCase):
    def test_question_is_cyan(self):
        lines = _multi_lines("Locations", MultiSelectState(items=["Jordan"]))
        self.assertTrue(any(CYAN in line and "Locations" in line for line in lines))

    def test_selected_row_is_green(self):
        state = MultiSelectState(items=["Jordan", "UAE"], selected=[True, False])
        lines = _multi_lines("Locations", state)
        jordan = next(line for line in lines if "Jordan" in line)
        uae = next(line for line in lines if "UAE" in line)
        self.assertIn(GREEN, jordan)
        self.assertNotIn(GREEN, uae)

    def test_highlighted_yes_no_is_green(self):
        state = SingleSelectState(items=["Yes", "No"], cursor=0)
        lines = _single_lines("Use these saved settings?", state)
        yes = next(line for line in lines if "Yes" in line)
        no = next(line for line in lines if "No" in line)
        self.assertIn(GREEN, yes)
        self.assertNotIn(GREEN, no)


class SearchBoxTests(unittest.TestCase):
    def test_empty_query_draws_colored_border_and_placeholder(self):
        lines = _search_box_lines("")
        joined = "\n".join(lines)
        self.assertIn("╭", joined)
        self.assertIn("╰", joined)
        self.assertIn("│", joined)
        self.assertTrue(any(MAGENTA in line for line in lines))
        self.assertTrue(any("Type a country" in line for line in lines))

    def test_query_appears_inside_the_box(self):
        lines = _search_box_lines("jordan")
        self.assertTrue(any("jordan" in line for line in lines))
        self.assertTrue(any("█" in line for line in lines))

    def test_country_picker_uses_the_search_box(self):
        state = SearchSelectState(items=["Jordan", "Germany"], query="ger")
        lines = _search_lines("Search and select countries", state)
        joined = "\n".join(lines)
        self.assertIn("╭", joined)
        self.assertTrue(any(MAGENTA in line for line in lines))
        self.assertTrue(any("ger" in line for line in lines))
        self.assertTrue(any("Germany" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
