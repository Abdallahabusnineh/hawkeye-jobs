import os
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import packaged
import paths
from background import Autostart

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging" / "windows"))
import build_msix  # noqa: E402

FAMILY = "abdallahabusnineh.HawkeyeJobs_dxq9w4c7vr5ty"
NS = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
      "desktop": "http://schemas.microsoft.com/appx/manifest/desktop/windows10",
      "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10"}


def _env_without(*names):
    return {k: v for k, v in os.environ.items() if k not in names}


class DetectionTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "checks the non-Windows answer")
    def test_never_packaged_off_windows(self):
        self.assertIsNone(packaged.package_family_name())
        self.assertFalse(packaged.is_packaged())
        self.assertFalse(packaged.launched_at_login())

    def test_local_state_dir(self):
        self.assertEqual(packaged.local_state_dir(FAMILY, r"C:\Users\sam\AppData\Local"),
                         Path(r"C:\Users\sam\AppData\Local") / "Packages" / FAMILY / "LocalState")


class DataDirTests(unittest.TestCase):
    def test_packaged_app_uses_its_local_state_folder(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {**_env_without("HAWKEYE_DATA_DIR"), "LOCALAPPDATA": tmp}, clear=True), \
                patch("paths.packaged.package_family_name", return_value=FAMILY):
            self.assertEqual(paths.data_dir(), Path(tmp) / "Packages" / FAMILY / "LocalState")
            self.assertTrue(paths.data_dir().is_dir())

    def test_env_override_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HAWKEYE_DATA_DIR": tmp}), \
                patch("paths.packaged.package_family_name", return_value=FAMILY):
            self.assertEqual(paths.data_dir(), Path(tmp))


class ImportEarlierInstallTests(unittest.TestCase):
    """Moving from the GitHub installer to the Store app keeps the jobs and settings."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.old, self.new = root / "hawkeye-jobs", root / "LocalState"
        self.old.mkdir()
        with sqlite3.connect(self.old / "hawkeye.db") as db:
            db.execute("CREATE TABLE jobs (url TEXT)")
            db.execute("INSERT INTO jobs VALUES ('https://a.com/1')")
        db.close()
        (self.old / "config.json").write_text('{"keywords": ["Flutter"]}', encoding="utf-8")
        self.patches = [patch.dict(os.environ, _env_without("HAWKEYE_DATA_DIR"), clear=True),
                        patch("packaged.is_packaged", return_value=True),
                        patch("paths.data_dir", side_effect=lambda: self._new()),
                        patch("paths.unpackaged_data_dir", return_value=self.old)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def _new(self):
        self.new.mkdir(exist_ok=True)
        return self.new

    def test_first_launch_copies_the_database_and_settings(self):
        import app
        with self.assertLogs("app", "INFO"):
            app._import_unpackaged_data()
        db = sqlite3.connect(self.new / "hawkeye.db")
        try:
            self.assertEqual(db.execute("SELECT url FROM jobs").fetchall(), [("https://a.com/1",)])
        finally:
            db.close()
        self.assertIn("Flutter", (self.new / "config.json").read_text(encoding="utf-8"))

    def test_never_overwrites_data_the_store_app_already_has(self):
        import app
        self._new()
        (self.new / "config.json").write_text('{"keywords": ["Mine"]}', encoding="utf-8")
        app._import_unpackaged_data()
        self.assertIn("Mine", (self.new / "config.json").read_text(encoding="utf-8"))
        self.assertFalse((self.new / "hawkeye.db").exists())

    def test_not_packaged_does_nothing(self):
        import app
        with patch("packaged.is_packaged", return_value=False):
            app._import_unpackaged_data()
        self.assertFalse(self.new.exists())


class FakeStartupTask:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.calls = []

    def is_enabled(self):
        return self.enabled

    def enable(self):
        self.calls.append("enable")
        self.enabled = True

    def disable(self):
        self.calls.append("disable")
        self.enabled = False


class StoreAutostartTests(unittest.TestCase):
    def test_store_app_uses_its_startup_task_not_the_run_key(self):
        from test_background import FakeRegistry
        registry, task = FakeRegistry(), FakeStartupTask()
        autostart = Autostart(platform="win32", registry=registry, store_task=task, command=["x.exe"])
        autostart.apply(True)
        self.assertTrue(autostart.is_enabled())
        autostart.apply(False)
        self.assertFalse(autostart.is_enabled())
        self.assertEqual(task.calls, ["enable", "disable"])
        self.assertEqual(registry.values, {})


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.layout = build_msix.write_layout(Path(self.tmp.name) / "layout", "1.2.3",
                                              dist=Path(self.tmp.name) / "no-dist")
        self.manifest = ET.parse(self.layout / "AppxManifest.xml").getroot()

    def test_store_version_has_four_parts_ending_in_zero(self):
        self.assertEqual(build_msix.store_version("1.2.3"), "1.2.3.0")
        with self.assertRaises(ValueError):
            build_msix.store_version("1.2")
        self.assertEqual(self.manifest.find("m:Identity", NS).get("Version"), "1.2.3.0")

    def test_identity_matches_partner_center(self):
        identity = self.manifest.find("m:Identity", NS)
        self.assertEqual(identity.get("Name"), "abdallahabusnineh.HawkeyeJobs")
        self.assertEqual(identity.get("Publisher"), "CN=C6E75268-01DD-4536-94C5-AC3A8C2B5762")
        self.assertEqual(self.manifest.find("m:Properties/m:PublisherDisplayName", NS).text, "abdallah abusnineh")

    def test_startup_task_id_matches_the_code(self):
        task = self.manifest.find(".//desktop:StartupTask", NS)
        self.assertEqual(task.get("TaskId"), packaged.STARTUP_TASK_ID)
        self.assertEqual(task.get("Enabled"), "false")  # off until the user turns it on

    def test_every_logo_has_its_images(self):
        assets = self.layout / "Assets"
        visual = self.manifest.find(".//uap:VisualElements", NS)
        logos = [visual.get("Square150x150Logo"), visual.get("Square44x44Logo"),
                 visual.find("uap:DefaultTile", NS).get("Wide310x150Logo"),
                 self.manifest.find("m:Properties/m:Logo", NS).text]
        for logo in logos:
            stem = Path(logo.replace("\\", "/")).stem
            self.assertTrue((assets / f"{stem}.scale-100.png").is_file(), logo)
        from PIL import Image
        with Image.open(assets / "Wide310x150Logo.scale-200.png") as wide:
            self.assertEqual(wide.size, (620, 300))


if __name__ == "__main__":
    unittest.main()
