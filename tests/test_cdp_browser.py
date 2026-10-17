import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import browser
import cdp_browser


class FakeSocket:
    """Replies to each sent command; unsolicited events are mixed in before the reply."""

    def __init__(self, replies):
        self.replies = replies  # method -> result dict, or callable(params) -> result
        self.sent = []
        self.queue = []

    def settimeout(self, t):
        pass

    def send(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        reply = self.replies[msg["method"]]
        result = reply(msg["params"]) if callable(reply) else reply
        self.queue += [json.dumps({"method": "Page.frameNavigated", "params": {}}),
                       json.dumps({"id": msg["id"], **result})]

    def recv(self):
        return self.queue.pop(0)

    def close(self):
        pass


def driver_with(replies):
    d = cdp_browser.CdpDriver.__new__(cdp_browser.CdpDriver)
    d._ws = FakeSocket(replies)
    d._next_id = 0
    d._page_timeout = 2
    d._target = "T1"
    return d


def value(v):
    return {"result": {"result": {"value": v}}}


class CdpDriverTests(unittest.TestCase):
    def test_replies_are_matched_by_id_and_events_ignored(self):
        d = driver_with({"Runtime.evaluate": value("Bayt jobs")})
        self.assertEqual(d.title, "Bayt jobs")
        self.assertEqual(d._ws.sent[0]["params"]["expression"], "document.title")

    def test_never_enables_domains(self):
        d = driver_with({"Page.navigate": {"result": {"frameId": "F"}},
                         "Runtime.evaluate": value("complete")})
        with patch("time.sleep"):
            d.get("https://www.bayt.com/")
        methods = {m["method"] for m in d._ws.sent}
        self.assertEqual(methods, {"Page.navigate", "Runtime.evaluate"})   # no Runtime.enable / Page.enable

    def test_navigation_error_raises(self):
        d = driver_with({"Page.navigate": {"result": {"errorText": "net::ERR_NAME_NOT_RESOLVED"}}})
        with self.assertRaises(cdp_browser.CdpError):
            d.get("https://nope.invalid/")

    def test_protocol_error_raises(self):
        d = driver_with({"Runtime.evaluate": {"error": {"message": "Target closed"}}})
        with self.assertRaises(cdp_browser.CdpError):
            d.page_source

    def test_wait_css_polls_until_found(self):
        answers = iter([False, False, True])
        d = driver_with({"Runtime.evaluate": lambda p: value(next(answers))})
        with patch("time.sleep"):
            self.assertTrue(d.wait_css("li[data-job-id]", 5))
        self.assertIn('"li[data-job-id]"', d._ws.sent[0]["params"]["expression"])

    def test_execute_script_wraps_a_function_body(self):
        d = driver_with({"Runtime.evaluate": value(None)})
        d.execute_script("window.scrollBy(0, 100);")
        self.assertEqual(d._ws.sent[0]["params"]["expression"], "(function(){ window.scrollBy(0, 100); })()")


class CreateDriverFallbackTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(browser, "find_chrome", return_value="/Applications/Google Chrome")
        p.start()
        self.addCleanup(p.stop)

    def test_normal_path_is_undetected_chromedriver(self):
        uc_driver = MagicMock()
        with patch.object(browser, "_create_uc_driver", return_value=uc_driver), \
             patch.object(cdp_browser, "CdpDriver") as cdp:
            self.assertIs(browser.create_driver(), uc_driver)
        cdp.assert_not_called()

    def test_falls_back_to_cdp_when_chromedriver_is_blocked(self):
        blocked = OSError("[WinError 4551] An Application Control policy has blocked this file")
        with patch.object(browser, "_create_uc_driver", side_effect=blocked), \
             patch.object(cdp_browser, "CdpDriver") as cdp, self.assertLogs("browser", "WARNING"):
            driver = browser.create_driver(user_data_dir="/tmp/profile")
        self.assertIs(driver, cdp.return_value)
        cdp.assert_called_once_with("/Applications/Google Chrome", user_data_dir="/tmp/profile", headless=False)

    def test_environment_forces_cdp(self):
        with patch.dict("os.environ", {"HAWKEYE_BROWSER": "cdp"}), \
             patch.object(browser, "_create_uc_driver") as uc, patch.object(cdp_browser, "CdpDriver") as cdp:
            browser.create_driver()
        uc.assert_not_called()
        cdp.assert_called_once()

    def test_no_chrome_is_still_chrome_not_found(self):
        with patch.object(browser, "find_chrome", return_value=None), self.assertRaises(browser.ChromeNotFound):
            browser.create_driver()

    def test_wait_for_uses_the_cdp_wait(self):
        driver = MagicMock(spec=["wait_css"])
        driver.wait_css.return_value = True
        self.assertTrue(browser.wait_for(driver, ".x", 3))
        driver.wait_css.assert_called_once_with(".x", 3)



@unittest.skipIf(sys.platform == "win32", "SingletonLock is a symlink on macOS and Linux")
class ProfileInUseTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def lock_to(self, pid):
        import os
        os.symlink(f"Some-Mac-{pid}", self.dir / "SingletonLock")

    def test_no_lock_is_free(self):
        self.assertFalse(browser.profile_in_use(self.dir))

    def test_lock_of_a_running_chrome_is_in_use(self):
        import os
        self.lock_to(os.getpid())
        self.assertTrue(browser.profile_in_use(self.dir))

    def test_stale_lock_from_a_crashed_chrome_is_free(self):
        import subprocess
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.lock_to(dead.pid)
        self.assertFalse(browser.profile_in_use(self.dir))

if __name__ == "__main__":
    unittest.main()
