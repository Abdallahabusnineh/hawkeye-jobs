import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from filters import is_relevant_job, keyword_stems


class KeywordStemsTests(unittest.TestCase):
    def test_drops_generic_role_words(self):
        self.assertEqual(keyword_stems(["Flutter Developer"]), ["flutter"])

    def test_keeps_multiple_distinct_stems(self):
        self.assertEqual(
            keyword_stems(["Flutter Developer", "Dart"]),
            ["flutter", "dart"],
        )

    def test_keeps_phrase_when_only_generic_words(self):
        self.assertEqual(keyword_stems(["Senior Engineer"]), ["senior engineer"])


class RelevantJobTests(unittest.TestCase):
    def test_accepts_title_containing_stem(self):
        self.assertTrue(
            is_relevant_job("Senior Flutter Engineer", ["Flutter Developer"])
        )

    def test_rejects_unrelated_title(self):
        self.assertFalse(is_relevant_job("QA Engineer", ["Flutter Developer"]))

    def test_does_not_match_mobile_inside_automobile(self):
        self.assertFalse(
            is_relevant_job("Automobile Designer", ["Mobile Developer"])
        )

    def test_accepts_mobile_as_whole_word(self):
        self.assertTrue(
            is_relevant_job("Senior Mobile Engineer", ["Mobile Developer"])
        )

    def test_empty_keywords_keeps_everything(self):
        self.assertTrue(is_relevant_job("Any Title", []))


if __name__ == "__main__":
    unittest.main()
