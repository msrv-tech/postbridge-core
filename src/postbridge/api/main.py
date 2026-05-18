from postbridge.api.agent_internal import router as agent_internal_router
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import sentry_sdk
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from postbridge.api.app_public import router as app_public_router
from postbridge.api.live_sync import router as live_sync_router
from postbridge.api.publication_internal import router as publication_internal_router
from postbridge.api.service_internal import router as service_internal_router
from postbridge.botkit.platforms.telegram.runtime import setup_telegram_bot_webhook
from postbridge.config import get_settings, validate_base_settings
from postbridge.db import init_db
from postbridge.domain.errors import InternalError, PostbridgeError, ValidationError
from postbridge.i18n import get_i18n
from postbridge.observability.metrics import export_prometheus
from sentry_sdk.integrations.fastapi import FastApiIntegration


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Application lifespan context: validate settings and initialize the database."""
    settings = get_settings()
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            integrations=[FastApiIntegration()],
        )
    validate_base_settings(settings)
    init_db()
    yield


app = FastAPI(title="postbridge-core", version="0.1.0", lifespan=lifespan)
setup_telegram_bot_webhook(app)
app.include_router(app_public_router)
app.include_router(live_sync_router)
app.include_router(publication_internal_router)
app.include_router(service_internal_router)
app.include_router(agent_internal_router)


def _media_storage_dir() -> Path:
    """Local media directory aligned with get_settings().media_storage_path."""
    s = get_settings()
    raw = (s.media_storage_path or "").strip() or "/var/postbridge/media"
    return Path(raw)


@app.get("/media/{path:path}", include_in_schema=False)
def serve_media(path: str) -> FileResponse:
    """Serve files from MEDIA_STORAGE_PATH for on-premise live-sync media."""
    media_root = _media_storage_dir()
    full = (media_root / path).resolve()
    if not str(full).startswith(str(media_root.resolve())):
        raise HTTPException(status_code=403, detail="error.http.invalid_path")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="error.http.not_found")
    return FileResponse(full)


_ERROR_STATUS_BY_CODE = {
    "VALIDATION_MIGRATION_RUN_NOT_FOUND": 404,
    "VALIDATION_CONTENT_ITEM_NOT_FOUND": 404,
    "MEDIA_STORAGE_NOT_CONFIGURED": 503,
}


def _status_code_for_error(exc: PostbridgeError) -> int:
    """Resolve the HTTP response status from a Postbridge error code."""
    if exc.code in _ERROR_STATUS_BY_CODE:
        return _ERROR_STATUS_BY_CODE[exc.code]
    if exc.code.startswith("VALIDATION_"):
        return 422
    if exc.code.startswith("AUTH_"):
        return 403
    if exc.code.startswith("EXTERNAL_API_") or exc.code.startswith("EXTERNAL_AI_"):
        return 502
    if exc.code.startswith("INTERNAL_"):
        return 500
    return 500


def _translate_message_key(key: str, *, default: str | None = None, params: dict | None = None) -> str:
    return get_i18n().translate(key, params=params or {}, default=default)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Propagate X-Correlation-Id through request handling and the response."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


@app.exception_handler(PostbridgeError)
async def postbridge_error_handler(request: Request, exc: PostbridgeError):
    """Handle Postbridge domain errors with the unified JSON format."""
    return JSONResponse(
        status_code=_status_code_for_error(exc),
        content=exc.to_dict(correlation_id=request.state.correlation_id),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    """Map Pydantic validation errors into the Postbridge error format."""
    error = ValidationError(
        code="VALIDATION_REQUEST_INVALID",
        message="request payload validation failed",
        message_key="error.validation.request_invalid",
        details={"errors": exc.errors()},
    )
    return JSONResponse(
        status_code=422,
        content=error.to_dict(correlation_id=request.state.correlation_id),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Wrap unhandled exceptions as InternalError responses."""
    error = InternalError(
        "Unhandled API error",
        message_key="error.internal.unhandled_api_error",
        details={"exception_type": type(exc).__name__},
    )
    return JSONResponse(
        status_code=_status_code_for_error(error),
        content=error.to_dict(correlation_id=request.state.correlation_id),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, str) and detail.startswith("error."):
        detail = _translate_message_key(detail, default=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": "HTTP_ERROR",
            "message": detail,
            "message_key": exc.detail if isinstance(exc.detail, str) and exc.detail.startswith("error.") else None,
            "params": {},
            "details": {},
            "source": "core",
            "retryable": False,
            "correlation_id": request.state.correlation_id,
        },
    )


def _web_dist_dir() -> Path | None:
    """Return the built Core frontend directory when it is available."""
    candidates = [
        Path.cwd() / "web" / "dist",
        Path(__file__).resolve().parents[3] / "web" / "dist",
        Path(__file__).resolve().parent.parent / "web",
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


@app.get("/web", response_class=HTMLResponse, include_in_schema=False)
@app.get("/web/", response_class=HTMLResponse, include_in_schema=False)
def web_ui():
    """Serve the Core frontend app."""
    web_dir = _web_dist_dir()
    if web_dir is None:
        title = _translate_message_key("web.core.title", default="Postbridge Core")
        body = _translate_message_key("web.core.not_found", default="Web UI not found")
        return HTMLResponse(f"<h1>{title}</h1><p>{body}</p>", status_code=404)
    return FileResponse(web_dir / "index.html")


@app.get("/web/{path:path}", include_in_schema=False)
def web_asset_or_route(path: str):
    """Serve built frontend assets and fall back to index.html for client routes."""
    web_dir = _web_dist_dir()
    if web_dir is None:
        raise HTTPException(status_code=404, detail="error.http.not_found")
    full = (web_dir / path).resolve()
    if str(full).startswith(str(web_dir.resolve())) and full.is_file():
        return FileResponse(full)
    return FileResponse(web_dir / "index.html")


@app.get("/health")
def healthcheck() -> dict[str, str]:
    """Check Core API availability."""
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    """Return Prometheus metrics."""
    return export_prometheus()


_ROOT_FRONTEND_API_PREFIXES = (
    "api",
    "core",
    "health",
    "internal",
    "media",
    "metrics",
)


def _root_frontend_response(path: str = ""):
    """Serve the shared frontend at root for hosted deployments."""
    first_segment = path.split("/", 1)[0]
    if first_segment in _ROOT_FRONTEND_API_PREFIXES:
        raise HTTPException(status_code=404, detail="error.http.not_found")
    web_dir = _web_dist_dir()
    if web_dir is None:
        raise HTTPException(status_code=404, detail="error.http.not_found")
    full = (web_dir / path).resolve()
    if path and str(full).startswith(str(web_dir.resolve())) and full.is_file():
        return FileResponse(full)
    return FileResponse(web_dir / "index.html")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root_web_ui():
    if get_settings().postbridge_app_mode == "selfhost":
        return RedirectResponse(url="/web", status_code=307)
    return _root_frontend_response()


@app.get("/{path:path}", include_in_schema=False)
def root_web_asset_or_route(path: str):
    return _root_frontend_response(path)
