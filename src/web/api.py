"""
api.py — Endpoints the UI calls (all local; see server.py for the guards)
==========================================================================
Responses are data and codes only — the UI owns every displayed word.
"""

import json
import logging
import queue
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from countries import localized_countries
from runner import NoSearchSettings, RunInProgress
from settings import LANGUAGES, USER_APP_KEYS, SettingsError, validate_search_settings
from store import STATUSES
from web.redact import redact

log = logging.getLogger(__name__)
router = APIRouter()

EMPTY_SETTINGS = {"keywords": [], "locations": [], "sources": []}


def _services(request: Request):
    return request.app.state.services


def _version_tuple(text):
    try:
        return tuple(int(part) for part in str(text).lstrip("vV").split("."))
    except ValueError:
        return ()


@router.get("/state")
def get_state(request: Request):
    s = _services(request)
    app = s.load_app_settings()
    profile = s.linkedin_profile_dir()
    with s.store_factory() as store:
        counts = store.job_counts()
    latest = app.get("latest_version")
    return {
        "version": s.version,
        "onboarding_done": app["onboarding_done"],
        "language": app["language"],
        "theme": app["theme"],
        "chrome": {"found": bool(s.find_chrome())},
        "linkedin_profile": profile.exists() and any(profile.iterdir()),
        "run": s.manager.current,
        "job_counts": counts,
        "has_settings": s.load_config() is not None,
        "update": {"available": bool(latest) and _version_tuple(latest) > _version_tuple(s.version),
                   "latest": latest},
    }


@router.get("/settings")
def get_settings(request: Request):
    return _services(request).load_config() or EMPTY_SETTINGS


@router.get("/app-settings")
def get_app_settings(request: Request):
    return _services(request).load_app_settings()


@router.get("/countries")
def get_countries(request: Request, lang: str = Query("ar")):
    if lang not in LANGUAGES:
        raise HTTPException(status_code=422, detail={"lang": "invalid"})
    return localized_countries(lang, _services(request).base_countries())


@router.get("/jobs")
def get_jobs(request: Request, status: str | None = None, source: str | None = None, text: str | None = None,
             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    include_dismissed = status == "all"
    if status == "all":
        status = None
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail={"status": "invalid"})
    with _services(request).store_factory() as store:
        jobs = store.list_jobs(status=status, source=source, text=text, include_dismissed=include_dismissed,
                               limit=limit, offset=offset)
        total = store.count_jobs(status=status, source=source, text=text, include_dismissed=include_dismissed)
    return {"jobs": jobs, "total": total}


@router.get("/runs")
def get_runs(request: Request, limit: int = Query(20, ge=1, le=100)):
    with _services(request).store_factory() as store:
        return {"runs": store.list_runs(limit=limit)}


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: int):
    with _services(request).store_factory() as store:
        details = store.run_details(run_id)
    if details is None:
        raise HTTPException(status_code=404, detail={"run": "not_found"})
    return details


KEEPALIVE_SECONDS = 15


@router.put("/settings")
def put_settings(request: Request, body: dict):
    s = _services(request)
    allowed = {c["id"] for c in s.base_countries()}
    try:
        cleaned = validate_search_settings(body, allowed)
    except SettingsError as exc:
        return JSONResponse({"errors": exc.errors}, status_code=422)
    s.save_config(cleaned)
    return cleaned


@router.patch("/app-settings")
def patch_app_settings(request: Request, body: dict):
    unknown = {key: "unknown" for key in body if key not in USER_APP_KEYS}
    if unknown:
        return JSONResponse({"errors": unknown}, status_code=422)
    try:
        return _services(request).save_app_settings(body)
    except SettingsError as exc:
        return JSONResponse({"errors": exc.errors}, status_code=422)


@router.patch("/jobs/{job_id}")
def patch_job(request: Request, job_id: str, body: dict):
    status = body.get("status")
    if status not in STATUSES:
        return JSONResponse({"errors": {"status": "invalid"}}, status_code=422)
    with _services(request).store_factory() as store:
        found = store.set_status(job_id, status)
    if not found:
        raise HTTPException(status_code=404, detail={"job": "not_found"})
    return {"ok": True}


@router.post("/runs", status_code=202)
def post_run(request: Request):
    try:
        run_id = _services(request).manager.start("manual")
    except RunInProgress:
        return JSONResponse({"error": "run_in_progress"}, status_code=409)
    except NoSearchSettings:
        return JSONResponse({"error": "no_settings"}, status_code=422)
    return {"run_id": run_id}  # None while the run is still starting


@router.post("/runs/current/cancel", status_code=202)
def cancel_run(request: Request):
    if not _services(request).manager.cancel():
        return JSONResponse({"error": "not_running"}, status_code=409)
    return {"ok": True}


def _event_stream(manager, q):
    try:
        yield "retry: 3000\n\n"
        while manager.is_subscribed(q):
            try:
                event = q.get(timeout=KEEPALIVE_SECONDS)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        manager.unsubscribe(q)


@router.get("/events")
def get_events(request: Request):
    manager = _services(request).manager
    q = manager.subscribe()
    return StreamingResponse(_event_stream(manager, q), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


ISSUE_URL = "https://github.com/Abdallahabusnineh/hawkeye-jobs/issues/new"
LOG_LINES = 200
MAX_ISSUE_URL = 6000  # browsers and GitHub reject very long URLs
UPDATE_CACHE_SECONDS = 24 * 3600


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


@router.post("/open-url", status_code=204)
def open_url(request: Request, body: dict):
    url = body.get("url")
    parsed = urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return JSONResponse({"errors": {"url": "invalid"}}, status_code=422)
    _services(request).open_url(url)


@router.post("/open-data-folder", status_code=204)
def open_data_folder(request: Request):
    s = _services(request)
    s.open_path(s.data_dir())


@router.post("/linkedin/login", status_code=202)
def linkedin_login(request: Request):
    s = _services(request)
    if s.manager.is_running:
        return JSONResponse({"error": "run_in_progress"}, status_code=409)
    chrome = s.find_chrome()
    if not chrome:
        return JSONResponse({"error": "chrome_not_found"}, status_code=409)
    s.launch([chrome, f"--user-data-dir={s.linkedin_profile_dir()}", "https://www.linkedin.com/login"])
    return {"ok": True}


def _issue_url(s, lines):
    """Build the prefilled issue URL, dropping the oldest log lines until it is short enough."""
    while True:
        excerpt = redact("\n".join(lines), home=s.home, user=s.user)
        body = (f"**Version:** {s.version}\n**System:** {s.os_name}\n\n"
                "**What happened?**\n\n\n"
                f"<details><summary>Log (last {len(lines)} lines, personal data removed)</summary>\n\n"
                f"```\n{excerpt}\n```\n</details>\n")
        url = f"{ISSUE_URL}?{urlencode({'title': 'Problem report', 'body': body})}"
        if len(url) <= MAX_ISSUE_URL or not lines:
            return url
        lines = lines[max(1, len(lines) // 10):]


@router.post("/report-problem")
def report_problem(request: Request):
    s = _services(request)
    try:
        lines = s.log_path().read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_LINES:]
    except OSError:
        lines = []
    url = _issue_url(s, lines)
    s.open_url(url)
    return {"url": url}


@router.get("/update")
def check_update(request: Request):
    s = _services(request)
    app = s.load_app_settings()
    if not app["update_check"]:
        return {"available": False, "disabled": True}
    checked = _parse_iso(app.get("last_update_check"))
    latest = app.get("latest_version")
    if checked is None or s.clock() - checked >= UPDATE_CACHE_SECONDS:
        patch = {}
        try:
            latest = str(s.fetch_latest_release().get("tag_name", "")).lstrip("vV") or None
            patch["latest_version"] = latest
        except Exception:
            log.warning("Update check failed", exc_info=True)  # keep what we knew; retry in 24 h
        patch["last_update_check"] = _iso(s.clock())
        s.save_app_settings(patch)
        checked = s.clock()
    return {"current": s.version, "latest": latest, "checked": _iso(checked),
            "available": bool(latest) and _version_tuple(latest) > _version_tuple(s.version)}


@router.post("/reset", status_code=204)
def reset(request: Request, body: dict):
    s = _services(request)
    if body.get("confirm") != "DELETE":
        return JSONResponse({"errors": {"confirm": "required"}}, status_code=422)
    if s.manager.is_running:
        return JSONResponse({"error": "run_in_progress"}, status_code=409)
    probe = s.store_factory()
    db_path = probe.path
    probe.close()
    root = Path(s.data_dir()).resolve()
    if Path(db_path).parent.resolve() != root:
        log.warning("Reset refused: database is not directly inside the data folder")
        return JSONResponse({"error": "outside_data_dir"}, status_code=409)
    for suffix in ("", "-wal", "-shm"):
        target = root / (Path(db_path).name + suffix)
        if target.is_symlink() or target.is_file():
            target.unlink()  # unlink removes a symlink itself, never its target
