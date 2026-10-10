import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import email_sender

JOBS = [{"title": "Java", "url": "https://a.com/1", "description": "Acme", "source": "Bayt"}]
CREDS = {"GMAIL_USER": "me@example.com", "GMAIL_PASS": "pw"}


class BuildHtmlTests(unittest.TestCase):
    def test_urls_and_text_are_escaped(self):
        job = {"title": "C++ <b>Dev</b>", "url": 'https://a.com/1?x=1&y="2"', "description": "A & B <i>",
               "source": "Bayt", "keyword": "<k>", "date_found": "d"}
        out = email_sender._build_html([job])
        self.assertIn('href="https://a.com/1?x=1&amp;y=&quot;2&quot;"', out)
        self.assertIn("C++ &lt;b&gt;Dev&lt;/b&gt;", out)
        self.assertIn("A &amp; B &lt;i&gt;", out)
        self.assertNotIn("<b>Dev", out)
        self.assertNotIn("<k>", out)


class SendEmailTests(unittest.TestCase):
    def test_empty_list_is_success(self):
        with patch("email_sender.smtplib.SMTP_SSL") as smtp:
            self.assertTrue(email_sender.send_email([]))
        smtp.assert_not_called()

    def test_not_configured_returns_false(self):
        with patch.dict("os.environ", {"GMAIL_USER": "", "GMAIL_PASS": ""}), \
             patch("email_sender.smtplib.SMTP_SSL") as smtp, patch("sys.stdout"):
            self.assertFalse(email_sender.send_email(JOBS))
        smtp.assert_not_called()

    def test_smtp_failure_returns_false(self):
        with patch.dict("os.environ", CREDS), \
             patch("email_sender.smtplib.SMTP_SSL", side_effect=OSError("down")), patch("sys.stdout"):
            self.assertFalse(email_sender.send_email(JOBS))

    def test_success_returns_true(self):
        with patch.dict("os.environ", CREDS), \
             patch("email_sender.smtplib.SMTP_SSL") as smtp, patch("sys.stdout"):
            self.assertTrue(email_sender.send_email(JOBS))
        smtp.return_value.__enter__.return_value.sendmail.assert_called_once()

    def test_email_configured(self):
        with patch.dict("os.environ", CREDS):
            self.assertTrue(email_sender.email_configured())
        with patch.dict("os.environ", {"GMAIL_USER": "me@example.com", "GMAIL_PASS": ""}):
            self.assertFalse(email_sender.email_configured())


if __name__ == "__main__":
    unittest.main()
