"""Tests for the API's foundation (step 9a): health checks, the one error shape, request IDs, CORS and the API
settings. No database, no network."""

import pytest

from src.api.deps import get_database
from src.config import ConfigError, load_api_settings
from tests.api.conftest import FakeDatabase

pytestmark = pytest.mark.anyio


async def test_health_and_request_ids(bare_client):
    response = await bare_client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
    first, second = response.headers["X-Request-ID"], (await bare_client.get("/health")).headers["X-Request-ID"]
    assert len(first) == 32 and first != second


async def test_ready_depends_on_the_database(app, bare_client):
    assert (await bare_client.get("/ready")).json() == {"status": "ready"}
    app.dependency_overrides[get_database] = lambda: FakeDatabase(reachable=False)
    response = await bare_client.get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"


async def test_every_error_has_the_same_shape(bare_client):
    missing = await bare_client.get("/api/v1/nothing-here")
    assert missing.status_code == 404
    error = missing.json()["error"]
    assert set(error) == {"code", "message", "request_id"} and error["code"] == "not_found"
    assert error["request_id"] == missing.headers["X-Request-ID"]


@pytest.mark.db
async def test_invalid_requests_are_422_once_logged_in(client):
    # since step 9d the login is checked first: these ran without one before (an anonymous request is now a 401,
    # tested in test_api_accounts.py)
    invalid = await client.get("/api/v1/investigations/not-a-uuid")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert "investigation_id" in invalid.json()["error"]["message"]

    too_many = await client.get("/api/v1/papers?limit=1000")
    assert too_many.status_code == 422 and "limit" in too_many.json()["error"]["message"]


async def test_an_unexpected_error_hides_its_details(app, bare_client):
    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internal detail")

    response = await bare_client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "secret" not in response.text and "Traceback" not in response.text
    assert body["error"]["request_id"]


async def test_cors_allows_only_the_frontend(bare_client):
    allowed = await bare_client.options("/api/v1/investigations", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    other = await bare_client.options("/api/v1/investigations", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in other.headers


def test_api_settings():
    assert load_api_settings({}).frontend_origins == ("http://localhost:3000",)
    two = load_api_settings({"FRONTEND_ORIGIN": " https://eureka.app/, http://localhost:3000 "})
    assert two.frontend_origins == ("https://eureka.app", "http://localhost:3000")
    with pytest.raises(ConfigError, match="FRONTEND_ORIGIN"):
        load_api_settings({"FRONTEND_ORIGIN": "eureka.app"})


async def test_the_docs_list_every_route(bare_client):
    paths = set((await bare_client.get("/openapi.json")).json()["paths"])
    assert {"/health", "/ready", "/api/v1/investigations", "/api/v1/investigations/{investigation_id}/results",
            "/api/v1/candidates/{candidate_id}/proposal", "/api/v1/papers/{paper_id}"} <= paths
