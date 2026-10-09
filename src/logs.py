"""
logs.py — Logging setup
========================
All modules log through `logging.getLogger(__name__)`. setup_logging() sends
those records to <data>/hawkeye.log (1 MB × 3 rotating files). A packaged app
has no terminal, so this file is what users attach to bug reports.
"""

import logging
from logging.handlers import RotatingFileHandler

from paths import log_path

HANDLER_NAME = "hawkeye-file"
_NOISY_LOGGERS = ("urllib3", "selenium", "undetected_chromedriver", "WDM")


def setup_logging(level=logging.INFO) -> None:
    """Attach the rotating file handler to the root logger. Calling it twice is harmless."""
    root = logging.getLogger()
    if any(handler.name == HANDLER_NAME for handler in root.handlers):
        return
    handler = RotatingFileHandler(
        log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.name = HANDLER_NAME
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
