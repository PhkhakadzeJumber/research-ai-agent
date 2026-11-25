"""The FastAPI app (doc 11 §3).

Run it locally (database running):
    uvicorn src.api.main:app --reload        # http://127.0.0.1:8000, interactive docs at /docs

Routes live under /api/v1 (health checks at the root): reading stored data (step 9a), and starting and controlling
runs (step 9b: these call Claude, in background runs, src/api/runs.py).
Every route except the health checks and sign up / log in needs a login (step 9d: a session cookie, src/api/auth.py),
and each user sees only their own investigations.
"""

from __future__ import annotations

import logging

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import errors
from src.api.errors import REQUEST_ID_HEADER
from src.api.auth import LoginLimiter, install_origin_check
from src.api.routes import accounts, actions, candidates, health, investigations, papers, progress
from src.api.routes import billing as billing_routes
from src.api.routes import dev as dev_routes
from src.api.routes import steps as step_routes
from src.api import billing
from src.api.runs import Runs
from src.config import get_api_settings
from src.db.session import Database

API_PREFIX = "/api/v1"


def create_app(database: Database | None = None) -> FastAPI:
    """The app. `database` is for tests; by default one is made from the settings at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.database = database or Database.from_settings()
        try:
            try:  # runs don't survive a restart: credits they reserved go back (src/api/billing.py)
                settled = await billing.settle_interrupted(lambda: app.state.database.session())
                if settled:
                    logging.getLogger("eureka.billing").info("returned the credits of %d interrupted run(s)", settled)
            except Exception:  # noqa: BLE001 - a database that isn't up yet must not stop the server starting
                logging.getLogger("eureka.billing").exception("settling interrupted runs failed")
            yield
        finally:
            await app.state.runs.shutdown()  # runs stop where they stand; their saved state stays resumable
            await app.state.database.dispose()

    app = FastAPI(title="Eureka API", version="0.1.0", lifespan=lifespan,
                  description="Research idea discovery and validation: investigations, ideas, evidence, papers.")
    # background runs use the app's database, created at startup (tests replace app.state.runs)
    app.state.runs = Runs(lambda: app.state.database.session())
    app.add_middleware(CORSMiddleware, allow_origins=list(get_api_settings().frontend_origins),
                       allow_methods=["GET", "POST"], allow_headers=["*"], expose_headers=[REQUEST_ID_HEADER])
    errors.install(app)
    install_origin_check(app)  # refuses state-changing requests from other sites (step 9d)
    app.state.login_limiter = LoginLimiter()
    app.include_router(health.router)
    v1 = APIRouter(prefix=API_PREFIX)
    for module in (accounts, actions, investigations, progress, candidates, papers, billing_routes, step_routes,
                   dev_routes):
        v1.include_router(module.router)
    v1.include_router(billing_routes.public)
    app.include_router(v1)
    return app


app = create_app()
