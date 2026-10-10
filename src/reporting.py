"""
reporting.py — Tell the user about new jobs, then mark them reported
=====================================================================
Jobs are saved (and treated as "seen") as each query finishes. Telling the
user happens separately, after the run, so a job is only marked reported once
a notifier confirms it reached the user. Anything not confirmed — a failed
email, a process killed mid-run — is picked up by the next report.

Used by terminal mode (print + optional email) and the desktop app (desktop
notification). A notifier is any callable `notifier(jobs) -> bool`.
"""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

REPORT_LIMIT = 500  # most jobs reported at once; the rest come in the next report


@dataclass
class ReportResult:
    reported: int
    more_waiting: bool
    delivered: bool
    jobs: list = field(default_factory=list)


def report_pending(store, notifier, limit: int = REPORT_LIMIT) -> ReportResult:
    """Report up to `limit` unreported jobs (oldest first); mark them only if `notifier` returns True."""
    fetched = store.unnotified_jobs(limit=limit + 1)
    pending = fetched[:limit]
    more_waiting = len(fetched) > limit
    if not pending:
        return ReportResult(reported=0, more_waiting=False, delivered=True, jobs=[])
    delivered = bool(notifier(pending))
    if delivered:
        store.mark_notified([j["id"] for j in pending])
    else:
        log.info("Report of %d job(s) not delivered; they will be retried next time", len(pending))
    return ReportResult(reported=len(pending) if delivered else 0, more_waiting=more_waiting,
                        delivered=delivered, jobs=pending)
