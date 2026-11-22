"""The FastAPI app (doc 11 §3).

Run it locally (database running):
    uvicorn src.api.main:app --reload        # http://127.0.0.1:8000, interactive docs at /docs

Routes live under /api/v1 (health checks at the root). Step 9a: read-only routes over stored data, no Claude calls.
There is no login yet (step 9d), so run it on your own machine only: uvicorn listens on 127.0.0.1 by default.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import errors
from src.api.errors import REQUEST_ID_HEADER
from src.api.routes import candidates, health, investigations, papers
from src.config import get_api_settings
from src.db.session import Database

API_PREFIX = "/api/v1"


def create_app(database: Database | None = None) -> FastAPI:
    """The app. `database` is for tests; by default one is made from the settings at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.database = database or Database.from_settings()
        try:
            yield
        finally:
            await app.state.database.dispose()

    app = FastAPI(title="Eureka API", version="0.1.0", lifespan=lifespan,
                  description="Research idea discovery and validation: investigations, ideas, evidence, papers.")
    app.add_middleware(CORSMiddleware, allow_origins=list(get_api_settings().frontend_origins),
                       allow_methods=["GET", "POST"], allow_headers=["*"], expose_headers=[REQUEST_ID_HEADER])
    errors.install(app)
    app.include_router(health.router)
    v1 = APIRouter(prefix=API_PREFIX)
    for module in (investigations, candidates, papers):
        v1.include_router(module.router)
    app.include_router(v1)
    return app


app = create_app()
