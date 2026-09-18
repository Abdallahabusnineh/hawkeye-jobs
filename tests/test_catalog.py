import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catalog import expand_searches


class ExpandSearchesTests(unittest.TestCase):
    def test_linkedin_is_keyword_times_location(self):
        searches = expand_searches(
            ["Flutter Developer"],
            ["jordan", "uae"],
            ["linkedin"],
        )
        self.assertEqual(
            searches["linkedin"],
            [
                {"term": "Flutter Developer", "location": "Jordan"},
                {"term": "Flutter Developer", "location": "United Arab Emirates"},
            ],
        )

    def test_indeed_includes_country_code(self):
        searches = expand_searches(
            ["Python Developer"],
            ["uk"],
            ["indeed"],
        )
        self.assertEqual(
            searches["indeed"],
            [
                {
                    "term": "Python Developer",
                    "location": "United Kingdom",
                    "country": "uk",
                }
            ],
        )

    def test_bayt_uses_slugs_and_skips_unsupported_countries(self):
        searches = expand_searches(
            ["Flutter Developer"],
            ["jordan", "uk"],
            ["bayt"],
        )
        self.assertEqual(searches["bayt"], [("flutter-developer", "jordan")])

    def test_naukri_skips_non_gulf(self):
        searches = expand_searches(
            ["Flutter Developer"],
            ["uae", "germany"],
            ["naukrigulf"],
        )
        self.assertEqual(searches["naukrigulf"], [("flutter developer", "uae")])

    def test_google_remote_appends_remote_to_term(self):
        searches = expand_searches(
            ["Python Developer"],
            ["remote"],
            ["google_jobs"],
        )
        self.assertEqual(
            searches["google_jobs"],
            [("Python Developer remote", "")],
        )

    def test_linkedin_posts_build_hiring_phrases(self):
        searches = expand_searches(
            ["Python Developer"],
            ["jordan"],
            ["linkedin_posts"],
        )
        self.assertEqual(
            searches["linkedin_posts"],
            [
                "hiring Python Developer",
                "looking for Python Developer",
            ],
        )

    def test_unselected_sources_are_empty(self):
        searches = expand_searches(["X"], ["jordan"], ["linkedin"])
        self.assertEqual(searches["bayt"], [])
        self.assertEqual(searches["indeed"], [])


if __name__ == "__main__":
    unittest.main()
