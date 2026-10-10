"""
api.py — Endpoints the UI calls (all local; see server.py for the guards)
==========================================================================
Responses are data and codes only — the UI owns every displayed word.
"""

import json
import logging
import queue

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from countries import localized_countries
from runner import NoSearchSettings, RunInProgress
from settings import LANGUAGES, USER_APP_KEYS, SettingsError, validate_search_settings
from store import STATUSES

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
