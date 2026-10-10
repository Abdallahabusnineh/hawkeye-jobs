"""
api.py — Endpoints the UI calls (all local; see server.py for the guards)
==========================================================================
Responses are data and codes only — the UI owns every displayed word.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from countries import localized_countries
from settings import LANGUAGES
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
