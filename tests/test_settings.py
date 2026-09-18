import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import settings


class ParseSelectionTests(unittest.TestCase):
    def test_all(self):
        self.assertEqual(settings.parse_selection("all", 3), [0, 1, 2])
        self.assertEqual(settings.parse_selection("ALL", 3), [0, 1, 2])
        self.assertEqual(settings.parse_selection("0", 3), [0, 1, 2])

    def test_space_separated_one_based(self):
        self.assertEqual(settings.parse_selection("1 3", 4), [0, 2])

    def test_comma_separated(self):
        self.assertEqual(settings.parse_selection("2,3", 3), [1, 2])

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            settings.parse_selection("9", 2)


class ConfigRoundTripTests(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            data = {
                "keywords": ["Flutter Developer"],
                "locations": ["jordan", "uae"],
                "sources": ["linkedin", "indeed"],
            }
            settings.save_config(data, path)
            self.assertEqual(settings.load_config(path), data)

    def test_load_missing_returns_none(self):
        self.assertIsNone(settings.load_config(Path("/tmp/does-not-exist-hawkeye.json")))


FAKE_COUNTRIES = [
    {"id": "jo", "label": "Jordan"},
    {"id": "ae", "label": "United Arab Emirates"},
]


class CollectSettingsTests(unittest.TestCase):
    def test_reuses_saved_config_on_yes(self):
        saved = {
            "keywords": ["Flutter Developer"],
            "locations": ["jordan"],
            "sources": ["linkedin"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(saved), encoding="utf-8")
            with patch("builtins.input", return_value="Y"):
                result = settings.collect_settings(
                    path, interactive=False, countries=FAKE_COUNTRIES
                )
        self.assertEqual(result, saved)

    def test_prompts_when_user_declines_saved(self):
        saved = {
            "keywords": ["Old"],
            "locations": ["jordan"],
            "sources": ["linkedin"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(saved), encoding="utf-8")
            answers = iter([
                "n",
                "Flutter Developer",
                "1",
                "y",
                "1",
                "y",
            ])
            with patch("builtins.input", side_effect=lambda *a, **k: next(answers)):
                result = settings.collect_settings(
                    path, interactive=False, countries=FAKE_COUNTRIES
                )
            self.assertEqual(result["keywords"], ["Flutter Developer"])
            self.assertEqual(result["locations"], ["jo"])
            self.assertEqual(result["sources"], ["linkedin"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)

    def test_declining_country_confirm_lets_user_pick_again(self):
        saved = {
            "keywords": ["Old"],
            "locations": ["jordan"],
            "sources": ["linkedin"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(saved), encoding="utf-8")
            answers = iter([
                "n",
                "Flutter Developer",
                "1",
                "n",
                "2",
                "y",
                "1",
                "y",
            ])
            with patch("builtins.input", side_effect=lambda *a, **k: next(answers)):
                result = settings.collect_settings(
                    path, interactive=False, countries=FAKE_COUNTRIES
                )
            self.assertEqual(result["locations"], ["ae"])
            self.assertEqual(result["sources"], ["linkedin"])


class ConfirmChoicesTests(unittest.TestCase):
    def test_question_lists_the_chosen_names(self):
        question = settings.confirm_choices_question(
            "countries", ["Jordan", "Saudi Arabia"]
        )
        self.assertEqual(
            question,
            "Confirm these countries: Jordan, Saudi Arabia?",
        )

    def test_question_truncates_a_long_list(self):
        names = [f"C{i}" for i in range(20)]
        question = settings.confirm_choices_question("countries", names, limit=3)
        self.assertIn("C0, C1, C2", question)
        self.assertIn("+17 more", question)
        self.assertTrue(question.endswith("?"))


if __name__ == "__main__":
    unittest.main()
