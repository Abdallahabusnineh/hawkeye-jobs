import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import logs


class SetupLoggingTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler.name == logs.HANDLER_NAME:
                root.removeHandler(handler)
                handler.close()

    def test_writes_to_log_file_in_data_dir_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HAWKEYE_DATA_DIR": tmp}):
                logs.setup_logging()
                logs.setup_logging()
                ours = [h for h in logging.getLogger().handlers if h.name == logs.HANDLER_NAME]
                self.assertEqual(len(ours), 1)
                self.assertEqual(ours[0].maxBytes, 1_000_000)
                self.assertEqual(ours[0].backupCount, 3)
                logging.getLogger("hawkeye.test").info("hello log")
                ours[0].flush()
                self.assertIn("hello log", (Path(tmp) / "hawkeye.log").read_text(encoding="utf-8"))
                self.tearDown()


if __name__ == "__main__":
    unittest.main()
