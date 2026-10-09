"""
store.py — SQLite store for jobs, their statuses, and search runs
==================================================================
Replaces tracker.py / seen_jobs.json. Keeps every job found (not just a hash)
so the app can show history and let the user mark jobs as saved / applied /
dismissed.

Tables (see docs/superpowers/specs/2026-10-10-core-pipeline-design.md §5):
    runs        — one row per search run
    jobs        — one row per unique job URL (id = job_id(url))
    legacy_seen — hashes imported from the old seen_jobs.json (dedup only)

Threading:
    One Store per thread (sqlite3 connections are not shared). WAL mode lets
    the app read while a search writes. Call recover_interrupted_runs() once
    at process start — never while a search is running.
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from paths import db_path
from utils import clean_url

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STATUSES = ("new", "saved", "applied", "dismissed")
RUN_STATES = ("running", "done", "cancelled", "failed")

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
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        self.conn.executescript(_SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

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

    def start_run(self, settings: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs (started_at, state, settings_json) VALUES (?, 'running', ?)",
                (_now(), json.dumps(settings, ensure_ascii=False)),
            )
        return cur.lastrowid

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
        except (OSError, json.JSONDecodeError):
            log.warning("Could not read legacy seen-jobs file %s", path)
            return 0
        seen = data.get("seen") if isinstance(data, dict) else None
        hashes = [h for h in (seen or []) if isinstance(h, str) and h]
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

    def list_jobs(self, status=None, source=None, run_id=None, text=None,
                  include_dismissed=False, limit=100, offset=0) -> list:
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
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY first_seen_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
