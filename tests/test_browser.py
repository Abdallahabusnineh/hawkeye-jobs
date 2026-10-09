import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selenium.common.exceptions import NoSuchElementException

import browser


class FindChromeTests(unittest.TestCase):
    def test_finds_macos_app(self):
        with patch("browser.os.path.exists", side_effect=lambda p: p == browser.MAC_CHROME), \
             patch("browser.shutil.which", return_value=None):
            self.assertEqual(browser.find_chrome(), browser.MAC_CHROME)

    def test_finds_windows_local_appdata_install(self):
        local = os.path.join("C:", "Users", "sam", "AppData", "Local")
        expected = os.path.join(local, "Google", "Chrome", "Application", "chrome.exe")
        env = {"LOCALAPPDATA": local, "PROGRAMFILES": "", "PROGRAMFILES(X86)": ""}
        with patch.dict(os.environ, env), \
             patch("browser.os.path.exists", side_effect=lambda p: p == expected), \
             patch("browser.shutil.which", return_value=None):
            self.assertEqual(browser.find_chrome(), expected)

    def test_falls_back_to_path(self):
        def which(name):
            return "/usr/bin/google-chrome-stable" if name == "google-chrome-stable" else None
        with patch("browser.os.path.exists", side_effect=lambda p: p == "/usr/bin/google-chrome-stable"), \
             patch("browser.shutil.which", side_effect=which):
            self.assertEqual(browser.find_chrome(), "/usr/bin/google-chrome-stable")

    def test_returns_none_when_missing(self):
        with patch("browser.os.path.exists", return_value=False), \
             patch("browser.shutil.which", return_value=None):
            self.assertIsNone(browser.find_chrome())


class CreateDriverTests(unittest.TestCase):
    def test_raises_chrome_not_found_without_starting_chrome(self):
        with patch("browser.find_chrome", return_value=None), \
             patch("browser.uc.Chrome") as chrome:
            with self.assertRaises(browser.ChromeNotFound):
                browser.create_driver()
            chrome.assert_not_called()

    def test_passes_the_found_binary(self):
        with patch("browser.find_chrome", return_value="/opt/chrome"), \
             patch("browser._detect_chrome_major", return_value=130), \
             patch("browser.uc.Chrome") as chrome:
            browser.create_driver()
        kwargs = chrome.call_args.kwargs
        self.assertEqual(kwargs["browser_executable_path"], "/opt/chrome")
        self.assertEqual(kwargs["version_main"], 130)


class DetectVersionTests(unittest.TestCase):
    def test_env_override(self):
        with patch.dict(os.environ, {"HAWKEYE_CHROME_MAJOR": "123"}):
            self.assertEqual(browser._detect_chrome_major("/whatever"), 123)


class FakeDriver:
    def __init__(self, appear_after_calls=None):
        self.calls = 0
        self.appear_after_calls = appear_after_calls

    def find_element(self, by, value):
        self.calls += 1
        if self.appear_after_calls is None or self.calls < self.appear_after_calls:
            raise NoSuchElementException("not yet")
        return object()


class WaitForTests(unittest.TestCase):
    def test_true_when_element_appears(self):
        self.assertTrue(browser.wait_for(FakeDriver(appear_after_calls=2), "li", timeout=2))

    def test_false_on_timeout(self):
        self.assertFalse(browser.wait_for(FakeDriver(appear_after_calls=None), "li", timeout=0.3))


if __name__ == "__main__":
    unittest.main()
