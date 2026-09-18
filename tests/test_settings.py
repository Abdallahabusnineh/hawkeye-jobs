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
                "keywords": ["Python Developer"],
                "locations": ["jordan", "uae"],
                "sources": ["linkedin", "indeed"],
            }
            settings.save_config(data, path)
            self.assertEqual(settings.load_config(path), data)

    def test_load_missing_returns_none(self):
        self.assertIsNone(settings.load_config(Path("/tmp/does-not-exist-hawkeye.json")))


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
                result = settings.collect_settings(path)
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
                "Python Developer",
                "1",
                "1",
            ])
            with patch("builtins.input", side_effect=lambda *a, **k: next(answers)):
                result = settings.collect_settings(path)
            self.assertEqual(result["keywords"], ["Python Developer"])
            self.assertEqual(result["locations"], ["jordan"])
            self.assertEqual(result["sources"], ["linkedin"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)


if __name__ == "__main__":
    unittest.main()
