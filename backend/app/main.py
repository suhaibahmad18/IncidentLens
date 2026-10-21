import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.api.routes import router
from app.config import Settings, WebSettings, get_settings
from app.db.connection import connect
from app.rag.embeddings import get_embedder
from app.runs import store
from app.runs.service import InvestigationService, groq_client_factory

logger = logging.getLogger("incidentlens")


class HealthResponse(BaseModel):
    status: str
    service: str


def create_app(settings: Settings | None = None, investigations: InvestigationService | None = None) -> FastAPI:
    """Settings load at startup (not import), so the app can be created without a database."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = settings or get_settings()
        app.state.settings = resolved
        app.state.investigations = investigations or InvestigationService(
            resolved, get_embedder(), groq_client_factory(resolved)
        )
        try:
            with connect(resolved.database_url, autocommit=True) as conn:
                interrupted = store.fail_interrupted_runs(conn)
            if interrupted:
                logger.warning("marked %d interrupted run(s) as failed", interrupted)
        except psycopg.Error as exc:
            logger.warning("startup run cleanup skipped: %s", type(exc).__name__)
        yield

    app = FastAPI(title="IncidentLens API", lifespan=lifespan)
    web = WebSettings()

    origins = [origin.strip() for origin in web.cors_origins.split(",") if origin.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type"])

    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(request: Request, exc: psycopg.OperationalError):
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.error("unhandled %s on %s", type(exc).__name__, request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="incidentlens-api")

    app.include_router(router)

    if web.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=web.frontend_dist, html=True), name="frontend")
    return app


app = create_app()
