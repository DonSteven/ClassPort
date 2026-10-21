import asyncio
import logging
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db as db_module
from .config import get_settings

# Single source of truth for the running app's version: pyproject.toml.
# Read directly rather than via importlib.metadata because uv's project
# install in the Docker image doesn't always populate metadata, and the
# pyproject.toml is copied into /app at build time anyway.
def _read_version() -> str:
    try:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            return tomllib.load(f)["project"]["version"]
    except Exception:
        return "0.0.0"


APP_VERSION = _read_version()
READINESS_TIMEOUT_SECONDS = 2

# httpx logs every outbound request URL at INFO ("HTTP Request: POST <url>"),
# which leaked the Telegram bot token and n8n webhook path into docker logs
# (rotated 2026-04-28). Bumping the logger to WARNING keeps error/timeout
# diagnostics but suppresses the per-request URL line.
logging.getLogger("httpx").setLevel(logging.WARNING)
from .mcp_http import build_mcp_http_app


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Manage the Postgres pool's lifecycle. Opens on startup, closes on shutdown.

    Test environments swap `app.db._pool` directly via monkeypatch (see
    tests/conftest.py) and skip this lifespan; init_pool() is idempotent
    so calling it under those conditions is harmless either way.
    """
    await db_module.init_pool()
    try:
        yield
    finally:
        await db_module.close_pool()
from .routers import (
    auth as auth_router,
    dashboard as dashboard_router,
    courses as courses_router,
    slots as slots_router,
    exams as exams_router,
    study_topics as study_topics_router,
    deliverables as deliverables_router,
    tasks as tasks_router,
    events as events_router,
    lectures as lectures_router,
    oauth as oauth_router,
    files as files_router,
    settings as settings_router,
    internal as internal_router,
)


def create_app() -> FastAPI:
    settings = get_settings()

    # build_mcp_http_app() returns a per-request handler that spins up a
    # fresh MCP server + lifespan context for every inbound request.
    # Heavy compared to a long-lived session manager, but bulletproof
    # under ASGI runtimes that don't fire lifespan events reliably.
    mcp_app = build_mcp_http_app()

    # /api/docs (Swagger UI) and /api/openapi.json off by default. The schema
    # is recon assist for any attacker enumerating attack surface. Flip on
    # via EXPOSE_DOCS=true in .env when working locally.
    app = FastAPI(
        title="ClassPort API",
        version=APP_VERSION,
        docs_url="/api/docs" if settings.expose_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.expose_docs else None,
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Reset the per-request user-id contextvar at the start of every request.
    # FastAPI deps run in a per-request task copy of the parent context, so
    # leakage between requests is normally impossible — but if uvicorn ever
    # reuses a task (or in test-client edge cases) a stale value could carry
    # over. This middleware is belt-and-suspenders defense.
    @app.middleware("http")
    async def _reset_user_ctx(request, call_next):
        from .auth import set_current_user_id
        set_current_user_id(None)
        return await call_next(request)

    @app.get("/api/health/live")
    async def live() -> JSONResponse:
        return JSONResponse({"ok": True, "version": APP_VERSION})

    # Keep /api/health for deploy.sh, including checks after an image rollback.
    @app.get("/api/health")
    @app.get("/api/health/ready")
    async def ready() -> JSONResponse:
        from .services import storage as storage_svc

        out: dict = {"ok": False, "version": APP_VERSION}
        dependency = "db"
        try:
            # One budget covers pool acquisition, the query, and storage I/O.
            async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
                await db_module.fetchval("SELECT 1")
                out["db"] = "ok"
                dependency = "storage"
                await storage_svc.check_readable()
                out["storage"] = "ok"
                out["ok"] = True
        except TimeoutError:
            out[dependency] = "error: readiness check timed out"
        except Exception as exc:
            out[dependency] = f"error: {exc!s}"[:200]
        return JSONResponse(out, status_code=200 if out["ok"] else 503)

    # Mount routers under /api
    for r in [
        auth_router.router,
        dashboard_router.router,
        courses_router.router,
        slots_router.router,
        exams_router.router,
        study_topics_router.router,
        deliverables_router.router,
        tasks_router.router,
        events_router.router,
        lectures_router.router,
        files_router.router,
        settings_router.router,
        internal_router.router,
    ]:
        app.include_router(r, prefix="/api")

    # OAuth AS + well-known discovery live at the root (not /api/*) because
    # MCP discovery requires /.well-known/* at the resource server origin.
    app.include_router(oauth_router.router)

    # MCP Streamable HTTP endpoint at /mcp (auth-gated by the OAuth Bearer token).
    app.mount("/mcp", mcp_app)

    return app


app = create_app()
