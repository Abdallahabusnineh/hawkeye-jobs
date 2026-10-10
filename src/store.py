"""
store.py — SQLite store for jobs, their statuses, and search runs
==================================================================
Replaces tracker.py / seen_jobs.json. Keeps every job found (not just a hash)
so the app can show history and let the user mark jobs as saved / applied /
dismissed.

Tables (see docs/superpowers/specs/2026-10-10-core-pipeline-design.md §5):
    runs        — one row per search run (v3: trigger, search_id)
    run_queries — one row per query of a run, with its outcome (v3)
    jobs        — one row per unique job URL (id = job_id(url)); notified_at is
                  NULL until the job has been reported (v2)
    legacy_seen — hashes imported from the old seen_jobs.json (dedup only)
    site_requests — last request start time per host group, for throttling (v2)

Schema version 3 (PRAGMA user_version): v1 = runs/jobs/legacy_seen,
v2 adds jobs.notified_at and site_requests, v3 adds runs.trigger,
runs.search_id and run_queries.

Threading:
    One Store per thread (sqlite3 connections are not shared). WAL mode lets
    the app read while a search writes. Call recover_interrupted_runs() once
    at process start — never while a search is running.
"""

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from paths import db_path
from utils import clean_url

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3
STATUSES = ("new", "saved", "applied", "dismissed")
RUN_STATES = ("running", "done", "cancelled", "failed")
TRIGGERS = ("manual", "auto", "terminal")
_QUERY_STATUS = {"query_done": "done", "query_failed": "failed", "query_skipped": "skipped"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  state         TEXT NOT NULL CHECK (state IN ('running','done','cancelled','failed')),
  settings_json TEXT NOT NULL,
  found_count   INTEGER NOT NULL DEFAULT 0,
  new_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
  id                TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  url               TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  source            TEXT NOT NULL,
  location          TEXT NOT NULL DEFAULT '',
  location_id       TEXT,
  keyword           TEXT NOT NULL DEFAULT '',
  published         TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','saved','applied','dismissed')),
  status_changed_at TEXT,
  first_seen_at     TEXT NOT NULL,
  last_seen_at      TEXT NOT NULL,
  first_run_id      INTEGER REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS jobs_by_status_seen ON jobs (status, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS legacy_seen (id TEXT PRIMARY KEY);
"""

# Migration steps: (target version, statements). Each runs in its own
# BEGIN IMMEDIATE transaction that re-checks user_version first, so two
# processes opening an old DB at once cannot both apply a step. Jobs that
# existed before v2 count as already reported; on a fresh DB that UPDATE
# touches nothing.
_V2_STATEMENTS = [
    "ALTER TABLE jobs ADD COLUMN notified_at TEXT",
    "UPDATE jobs SET notified_at = first_seen_at",
    """CREATE TABLE IF NOT EXISTS site_requests (
  site            TEXT PRIMARY KEY,
  last_request_at REAL NOT NULL
)""",
]
_V3_STATEMENTS = [
    "ALTER TABLE runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual' "
    "CHECK (trigger IN ('manual','auto','terminal'))",
    "ALTER TABLE runs ADD COLUMN search_id INTEGER NOT NULL DEFAULT 1",
    """CREATE TABLE IF NOT EXISTS run_queries (
  run_id      INTEGER NOT NULL REFERENCES runs(id),
  idx         INTEGER NOT NULL,
  source      TEXT NOT NULL,
  keyword     TEXT NOT NULL DEFAULT '',
  location_id TEXT,
  status      TEXT NOT NULL CHECK (status IN ('done','failed','skipped')),
  found       INTEGER NOT NULL DEFAULT 0,
  new         INTEGER NOT NULL DEFAULT 0,
  detail      TEXT,
  PRIMARY KEY (run_id, idx)
)""",
]
_STEPS = [
    (1, [st.strip() for st in _SCHEMA.split(";") if st.strip()]),
    (2, _V2_STATEMENTS),
    (3, _V3_STATEMENTS),
]
_ADD_COLUMN = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_id(url: str) -> str:
    """MD5 of the cleaned URL — identical to the old tracker._job_id, so seen_jobs.json hashes still match."""
    cleaned = clean_url(url or "").strip().rstrip("/")
    return hashlib.md5(cleaned.encode()).hexdigest()


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else db_path()
        self.conn = sqlite3.connect(self.path, timeout=5.0)
        try:
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA busy_timeout = 5000")
            self.conn.execute("PRAGMA foreign_keys = ON")
            self._enable_wal()
            self._migrate()
        except BaseException:
            self.conn.close()  # a failed open must not leak the connection
            raise

    def _enable_wal(self) -> None:
        """Switch to WAL. Several processes creating a brand-new DB at once can hit
        'database is locked' here (the switch ignores busy_timeout), so retry briefly."""
        for attempt in range(5):
            try:
                self.conn.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                time.sleep(0.2)

    def _read_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self) -> None:
        version = self._read_version()
        for target, statements in _STEPS:
            if version < target:
                self._apply_step(target, statements)

    def _apply_step(self, target: int, statements: list) -> None:
        """Apply one step atomically; no-op if another opener already did."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if self._read_version() >= target:
                self.conn.execute("ROLLBACK")
                return
            skipped_columns = set()
            for statement in statements:
                match = _ADD_COLUMN.match(statement)
                if match:
                    table, column = match.groups()
                    existing = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")]
                    if column in existing:  # e.g. tables built by hand at an older version
                        skipped_columns.add(column)
                        continue
                if statement.startswith("UPDATE jobs SET notified_at") and "notified_at" in skipped_columns:
                    continue
                self.conn.execute(statement)
            self.conn.execute(f"PRAGMA user_version = {target}")
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── runs ──────────────────────────────────────────────────────────────

    def recover_interrupted_runs(self) -> int:
        """Mark runs left 'running' by a crashed/killed process as 'failed'."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE runs SET state = 'failed', finished_at = ? WHERE state = 'running'",
                (_now(),),
            )
        if cur.rowcount:
            log.info("Marked %d interrupted run(s) as failed", cur.rowcount)
        return cur.rowcount

    def start_run(self, settings: dict, trigger: str = "manual", search_id: int = 1) -> int:
        if trigger not in TRIGGERS:
            raise ValueError(f"Unknown trigger: {trigger}")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs (started_at, state, settings_json, trigger, search_id) "
                "VALUES (?, 'running', ?, ?, ?)",
                (_now(), json.dumps(settings, ensure_ascii=False), trigger, search_id),
            )
        return cur.lastrowid

    def record_query(self, run_id: int, event: dict) -> None:
        """Store one finished query (query_done / query_failed / query_skipped); other events are ignored."""
        status = _QUERY_STATUS.get(event.get("type"))
        if status is None:
            return
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO run_queries "
                "(run_id, idx, source, keyword, location_id, status, found, new, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, event["index"], event["source"], event.get("keyword") or "",
                 event.get("location_id"), status, int(event.get("found") or 0),
                 int(event.get("new") or 0), event.get("error") or event.get("reason")),
            )

    def run_details(self, run_id: int):
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json"))
        item["queries"] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM run_queries WHERE run_id = ? ORDER BY idx", (run_id,))]
        return item

    def last_finished_run(self, trigger=None):
        sql = "SELECT * FROM runs WHERE state != 'running'"
        params = []
        if trigger is not None:
            sql += " AND trigger = ?"
            params.append(trigger)
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json"))
        return item

    def finish_run(self, run_id: int, state: str, found: int, new: int) -> None:
        if state not in RUN_STATES or state == "running":
            raise ValueError(f"Not a final run state: {state}")
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET state = ?, finished_at = ?, found_count = ?, new_count = ? WHERE id = ?",
                (state, _now(), found, new, run_id),
            )

    def list_runs(self, limit: int = 20) -> list:
        rows = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["settings"] = json.loads(item.pop("settings_json"))
            result.append(item)
        return result

    # ── jobs ──────────────────────────────────────────────────────────────

    def import_legacy_seen_file(self, path) -> int:
        """Import hashes from the old seen_jobs.json. Safe to call every start. Returns how many were added."""
        path = Path(path)
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # ValueError: bad JSON or not UTF-8
            log.warning("Could not read legacy seen-jobs file %s", path)
            return 0
        seen = data.get("seen") if isinstance(data, dict) else None
        if not isinstance(seen, list):
            return 0
        hashes = [h for h in seen if isinstance(h, str) and h]
        with self.conn:
            before = self.conn.total_changes
            self.conn.executemany(
                "INSERT OR IGNORE INTO legacy_seen (id) VALUES (?)", [(h,) for h in hashes]
            )
            added = self.conn.total_changes - before
        if added:
            log.info("Imported %d previously seen job(s) from %s", added, path)
        return added

    def add_jobs(self, run_id: int, jobs: list) -> list:
        """Save jobs from one query. Returns only jobs never seen before (each with an "id" key)."""
        now = _now()
        new_jobs = []
        with self.conn:
            for job in jobs:
                url = str(job.get("url") or "").strip()
                if not url:
                    continue
                jid = job_id(url)
                if self.conn.execute("SELECT 1 FROM legacy_seen WHERE id = ?", (jid,)).fetchone():
                    continue
                updated = self.conn.execute(
                    "UPDATE jobs SET last_seen_at = ? WHERE id = ?", (now, jid)
                )
                if updated.rowcount:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO jobs (id, title, url, description, source, location, location_id,
                                      keyword, published, status, first_seen_at, last_seen_at, first_run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
                    """,
                    (
                        jid,
                        str(job.get("title") or ""),
                        url,
                        str(job.get("description") or ""),
                        str(job.get("source") or ""),
                        str(job.get("location") or ""),
                        job.get("location_id"),
                        str(job.get("keyword") or ""),
                        str(job.get("published") or ""),
                        now,
                        now,
                        run_id,
                    ),
                )
                new_jobs.append({**job, "id": jid})
        return new_jobs

    def set_status(self, job_id: str, status: str) -> bool:
        if status not in STATUSES:
            raise ValueError(f"Unknown status: {status}")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE jobs SET status = ?, status_changed_at = ? WHERE id = ?",
                (status, _now(), job_id),
            )
        return cur.rowcount > 0

    def job_counts(self) -> dict:
        counts = {status: 0 for status in STATUSES}
        for status, n in self.conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status"):
            counts[status] = n
        return counts

    def _job_where(self, status, source, run_id, text, include_dismissed):
        clauses, params = [], []
        if status is not None:
            if status not in STATUSES:
                raise ValueError(f"Unknown status: {status}")
            clauses.append("status = ?")
            params.append(status)
        elif not include_dismissed:
            clauses.append("status != 'dismissed'")
        if source:
            clauses.append("source = ?")
            params.append(source)
        if run_id is not None:
            clauses.append("first_run_id = ?")
            params.append(run_id)
        if text:
            clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            pattern = _like_pattern(text)
            params += [pattern, pattern]
        return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params

    def count_jobs(self, status=None, source=None, run_id=None, text=None, include_dismissed=False) -> int:
        where, params = self._job_where(status, source, run_id, text, include_dismissed)
        return self.conn.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]

    def list_jobs(self, status=None, source=None, run_id=None, text=None,
                  include_dismissed=False, limit=100, offset=0) -> list:
        where, params = self._job_where(status, source, run_id, text, include_dismissed)
        rows = self.conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY first_seen_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── reporting and request times (schema v2) ───────────────────────────

    def unnotified_jobs(self, limit: int = 500) -> list:
        """Jobs not yet reported (and not dismissed), oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE notified_at IS NULL AND status != 'dismissed' "
            "ORDER BY first_seen_at ASC, rowid ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_notified(self, job_ids: list) -> int:
        """Stamp notified_at on these jobs (if still NULL). Returns rows changed."""
        if not job_ids:
            return 0
        now = _now()
        with self.conn:
            before = self.conn.total_changes
            self.conn.executemany(
                "UPDATE jobs SET notified_at = ? WHERE id = ? AND notified_at IS NULL",
                [(now, jid) for jid in job_ids],
            )
            return self.conn.total_changes - before

    def site_last_requests(self) -> dict:
        rows = self.conn.execute("SELECT site, last_request_at FROM site_requests").fetchall()
        return {row["site"]: float(row["last_request_at"]) for row in rows}

    def save_site_requests(self, times: dict) -> None:
        """Upsert last request times; a stored time never moves backwards."""
        if not times:
            return
        with self.conn:
            self.conn.executemany(
                "INSERT INTO site_requests (site, last_request_at) VALUES (?, ?) "
                "ON CONFLICT(site) DO UPDATE SET "
                "last_request_at = MAX(last_request_at, excluded.last_request_at)",
                [(site, float(t)) for site, t in times.items()],
            )
