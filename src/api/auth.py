"""Accounts and login (step 9d): password hashing, the session cookie, the current user, the login attempt limit
and the cross-site request check.

- Passwords are hashed with Argon2 (OWASP's first recommendation); they are never stored or logged.
- Logging in creates a session: a random token goes to the browser in the `eureka_session` cookie (HttpOnly, so
  page scripts can't read it; SameSite=Lax; Secure unless EUREKA_COOKIE_SECURE=false). The database keeps only the
  token's SHA-256. Sessions last 30 days and are extended while used; logging out deletes the session.
- Cookies rather than tokens in the browser: the live progress stream (SSE) can't send headers but sends cookies,
  and an HttpOnly cookie can't be read by injected scripts. In deployment the frontend forwards /api/* to this API
  so the cookie stays first-party.
- Requests that change something (POST, PUT, PATCH, DELETE) coming from another site are refused (their Origin isn't
  FRONTEND_ORIGIN): with SameSite=Lax this blocks cross-site request forgery.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, FastAPI, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import SessionDep
from src.api.errors import ApiError, error_response
from src.config import get_api_settings
from src.db.models import User
from src.db.repositories.users import find_session
from src.timeutil import now

COOKIE_NAME = "eureka_session"
SESSION_DAYS = 30
SLIDE_AFTER = timedelta(hours=1)
"""A used session's expiry is pushed back at most once an hour (not on every request)."""
MIN_PASSWORD, MAX_PASSWORD = 10, 128
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_DAYS * 24 * 3600, httponly=True,
                        secure=get_api_settings().cookie_secure, samesite="lax", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=get_api_settings().cookie_secure,
                           samesite="lax")


async def authenticate(session: AsyncSession, token: str | None) -> User | None:
    """The user a session token belongs to, or None (no token, unknown, or expired). Extends a used session."""
    if not token:
        return None
    found = await find_session(session, token_hash(token))
    if found is None:
        return None
    row, user = found
    moment = now()
    if row.expires_at <= moment:
        return None
    if moment - row.last_seen_at >= SLIDE_AFTER:
        row.last_seen_at, row.expires_at = moment, moment + timedelta(days=SESSION_DAYS)
        await session.flush()
    return user


async def get_current_user(request: Request, session: SessionDep) -> User:
    user = await authenticate(session, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise ApiError(401, "not_logged_in", "Log in to continue.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


class LoginLimiter:
    """At most `attempts` failed logins per key (an email, an address) within `window` seconds. In memory: enough
    for one server process; a shared store would be needed with several."""

    def __init__(self, attempts: int = 5, window: float = 15 * 60) -> None:
        self.attempts, self.window = attempts, window
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        failures, cutoff = self._failures[key], time.monotonic() - self.window
        while failures and failures[0] < cutoff:
            failures.popleft()
        return failures

    def check(self, *keys: str) -> None:
        if any(len(self._recent(key)) >= self.attempts for key in keys):
            raise ApiError(429, "too_many_attempts", "Too many failed logins: wait 15 minutes and try again.")

    def failed(self, *keys: str) -> None:
        for key in keys:
            self._recent(key).append(time.monotonic())

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)


def install_origin_check(app: FastAPI) -> None:
    """Refuses state-changing requests sent by other sites (cross-site request forgery)."""
    allowed = set(get_api_settings().frontend_origins)

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in UNSAFE_METHODS and origin and origin.rstrip("/") not in allowed:
            return error_response(request, 403, "bad_origin", "This request came from another site and was refused.")
        return await call_next(request)
