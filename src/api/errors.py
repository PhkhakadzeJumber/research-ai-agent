"""One error shape for every failure (doc 11 §5): {"error": {"code", "message", "request_id"}}.

Every response carries an X-Request-ID header; an error's request_id is the same value, so a user can quote it and
the server log line can be found. Unexpected exceptions become a 500 with a generic message: no stack traces or
internal details reach the client (they go to the server log instead).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("eureka.api")

REQUEST_ID_HEADER = "X-Request-ID"
STATUS_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 409: "conflict",
                422: "invalid_request", 503: "unavailable"}


class ApiError(Exception):
    """Raised by routes for an expected failure: becomes {"error": {...}} with this status and code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or uuid.uuid4().hex


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    rid = request_id(request)
    return JSONResponse({"error": {"code": code, "message": message, "request_id": rid}}, status_code=status,
                        headers={REQUEST_ID_HEADER: rid})


def install(app: FastAPI) -> None:
    """Adds the request-ID middleware and the error handlers to the app."""

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        message = error.detail if isinstance(error.detail, str) else "Request failed."
        return error_response(request, error.status_code, STATUS_CODES.get(error.status_code, "error"), message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        problems = "; ".join(f"{'.'.join(str(part) for part in e['loc'] if part != 'query')}: {e['msg']}"
                             for e in error.errors())
        return error_response(request, 422, "invalid_request", problems or "Invalid request.")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        log.exception("unhandled error (request %s)", request_id(request))
        return error_response(request, 500, "internal_error",
                              "Something went wrong on our side. Quote the request ID if you report it.")
