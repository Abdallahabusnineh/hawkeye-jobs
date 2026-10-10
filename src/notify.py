"""
notify.py — Desktop notifications for new jobs
===============================================
The app reports new jobs with a native notification (macOS / Windows / Linux,
via desktop-notifier). A notifier always returns True: if the OS refuses or
cannot show notifications, the jobs are still visible as "New" in the app, and
treating them as unreported would make them pile up and re-notify forever.
"""

import logging

log = logging.getLogger(__name__)

APP_NAME = "Hawkeye Jobs"
_HEADLINES = {"ar": "وظائف جديدة: {n}", "en": "New jobs: {n}"}
_SHOWN_TITLES = 3


def format_message(jobs, language) -> tuple:
    template = _HEADLINES.get(language, _HEADLINES["en"])
    lines = [template.format(n=len(jobs))]
    lines += [f"• {job.get('title', '')}" for job in jobs[:_SHOWN_TITLES]]
    if len(jobs) > _SHOWN_TITLES:
        lines.append(f"+{len(jobs) - _SHOWN_TITLES}")
    return APP_NAME, "\n".join(lines)


class DesktopNotifier:
    def __init__(self, language_getter, backend=None):
        self._language = language_getter
        self._backend = backend

    def _get_backend(self):
        if self._backend is None:
            try:
                from desktop_notifier import DesktopNotifierSync
                self._backend = DesktopNotifierSync(app_name=APP_NAME)
            except Exception:
                log.warning("Desktop notifications are unavailable", exc_info=True)
                self._backend = False
        return self._backend

    def __call__(self, jobs) -> bool:
        if not jobs:
            return True
        title, message = format_message(jobs, self._language())
        backend = self._get_backend()
        if backend:
            try:
                backend.send(title=title, message=message)
            except Exception:
                log.warning("Could not show a desktop notification", exc_info=True)
        return True
