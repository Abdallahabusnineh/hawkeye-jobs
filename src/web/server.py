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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from web.api import router

STATIC_DIR = Path(__file__).resolve().parent / "static"
EVENTS_PATH = "/api/events"

# 'unsafe-eval' is required by the standard Alpine.js build (it compiles x-* expressions).
# Everything else is locked to this origin; the UI loads nothing from the internet.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


def _token_ok(request: Request, token: str) -> bool:
    supplied = request.headers.get("x-hawkeye-token")
    if supplied is None and request.url.path == EVENTS_PATH:
        supplied = request.query_params.get("token")
    return bool(supplied) and hmac.compare_digest(supplied.encode(), token.encode())


def create_app(*, token: str, allowed_hosts: set, services, static_dir: Path = STATIC_DIR) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            response = JSONResponse({"error": "forbidden"}, status_code=403)
        elif request.url.path.startswith("/api/") and not _token_ok(request, token):
            response = JSONResponse({"error": "forbidden"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    # The backend sends codes, never display text: replace FastAPI's English default bodies.
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        errors = {}
        for err in exc.errors():
            loc = err.get("loc") or ()
            field = str(loc[-1]) if len(loc) > 1 else "body"
            errors[field] = "invalid"
        return JSONResponse({"errors": errors or {"body": "invalid"}}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict):  # our own coded errors, e.g. {"lang": "invalid"}: keep FastAPI's shape
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        code = "not_found" if exc.status_code == 404 else f"http_{exc.status_code}"
        return JSONResponse({"error": code}, status_code=exc.status_code, headers=exc.headers)

    app.include_router(router, prefix="/api")
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app
