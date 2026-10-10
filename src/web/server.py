"""
server.py — The app's local web server (127.0.0.1 only)
========================================================
The window (pywebview) talks to this FastAPI app on the user's own machine.
Nothing here is reachable from other computers. Two guards keep other web
pages in the user's browser out:
  * Host must be 127.0.0.1:<port> / localhost:<port> (blocks DNS rebinding);
  * every /api/* call must carry the per-launch random token
    (X-Hawkeye-Token header, or ?token= on GET /api/events only, because
    EventSource cannot set headers).
Static files (the UI) are served without the token; they contain no data.
"""

import hmac
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from web.api import router

STATIC_DIR = Path(__file__).resolve().parent / "static"
EVENTS_PATH = "/api/events"


def create_app(*, token: str, allowed_hosts: set, services, static_dir: Path = STATIC_DIR) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-hawkeye-token")
            if supplied is None and request.url.path == EVENTS_PATH:
                supplied = request.query_params.get("token")
            if not supplied or not hmac.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    app.include_router(router, prefix="/api")
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app
