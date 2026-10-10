import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import utils

TODAY = date(2026, 10, 10)


class IsRecentTests(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(utils.is_recent(text, today=TODAY), expected, text)

    def test_window_is_seven_days(self):
        self.assertEqual(utils.MAX_AGE_DAYS, 7)
        self.assertFalse(hasattr(utils, "is_within_48h"))

    def test_unknown_values_are_kept(self):
        for text in ("", None, "none", "nan", "N/A", "-", "sometime"):
            self.check(text, True)

    def test_immediate_and_yesterday(self):
        for text in ("just now", "Today", "moments ago", "Yesterday"):
            self.check(text, True)

    def test_hours(self):
        self.check("3 hours ago", True)
        self.check("168 hours ago", True)
        self.check("169 hours ago", False)
        self.check("5 hrs ago", True)
        self.check("200 hrs", False)

    def test_days(self):
        self.check("1 day ago", True)
        self.check("7 days ago", True)
        self.check("8 days ago", False)

    def test_weeks_months_years_and_plus(self):
        self.check("1 week ago", True)
        self.check("2 weeks ago", False)
        self.check("1 month ago", False)
        self.check("1 year ago", False)
        self.check("30+ days ago", False)

    def test_day_month_uses_calendar_dates(self):
        self.check("3 Oct", True)       # 7 days
        self.check("2 Oct", False)      # 8 days
        self.check("8 October", True)
        self.check("29 Sep", False)

    def test_day_month_in_the_future_rolls_back_a_year(self):
        self.check("12 Oct", False)
        self.assertTrue(utils.is_recent("29 Dec", today=date(2026, 1, 2)))
        self.assertFalse(utils.is_recent("20 Dec", today=date(2026, 1, 2)))

    def test_day_month_tolerates_local_date_one_ahead_of_utc(self):
        self.assertTrue(utils.is_recent("10 Oct", today=date(2026, 10, 9)))
        self.assertFalse(utils.is_recent("11 Oct", today=date(2026, 10, 9)))

    def test_iso_datetime_with_offset_is_not_an_old_marker(self):
        self.check("2026-10-08T22:30:00+03:00", True)
        self.check("2026-09-01T22:30:00+03:00", False)
        self.check("30+ days ago", False)

    def test_leap_day_rolled_back_into_a_non_leap_year_does_not_raise(self):
        self.assertFalse(utils.is_recent("29 Feb", today=date(2028, 1, 15)))

    def test_leap_day(self):
        self.assertTrue(utils.is_recent("29 Feb", today=date(2028, 3, 1)))

    def test_iso_dates_use_calendar_dates(self):
        self.check("2026-10-08", True)
        self.check("2026-10-03", True)
        self.check("2026-10-02", False)
        self.check("2026-10-03T23:59:00", True)
        self.check("2026-10-02T23:59:00", False)

    def test_today_defaults_to_utc_now(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(utils.is_recent(now.strftime("%Y-%m-%d")))
        self.assertFalse(utils.is_recent("2000-01-01"))


if __name__ == "__main__":
    unittest.main()
