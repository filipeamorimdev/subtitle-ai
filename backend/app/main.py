"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.ai_routes import router as ai_router
from app.api.localization_routes import router as localization_router
from app.core.config import get_app_config
from app.core.logging import setup_logging
from app.db import init_db
from app.jobs.scanner import scanner
from app.jobs.worker import worker


def _resolve_frontend_dist() -> Path | None:
    config = get_app_config()
    if config.frontend_dist and config.frontend_dist.exists():
        return config.frontend_dist
    candidates = [
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
        Path("/app/frontend/dist"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_app_config()
    setup_logging(config.log_level)
    config.ensure_directories()
    init_db()
    from app.ai.bootstrap import bootstrap_providers
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        bootstrap_providers(session)
    finally:
        session.close()
    await worker.start()
    await scanner.start()
    # Resume active localization tasks after restart.
    try:
        from app.localization.planner import TaskPlanner

        resume_session = get_session_factory()()
        try:
            await TaskPlanner(resume_session).plan_all_active()
        finally:
            resume_session.close()
    except Exception:  # noqa: BLE001
        pass
    yield
    await scanner.stop()
    await worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Subtitle AI", version="0.3.0a2", lifespan=lifespan)
    app.include_router(router)
    app.include_router(ai_router)
    app.include_router(localization_router)

    dist = _resolve_frontend_dist()
    if dist is not None:
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            # Do not shadow API
            if full_path.startswith("api"):
                return {"detail": "Not Found"}
            file_path = dist / full_path
            if full_path and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
