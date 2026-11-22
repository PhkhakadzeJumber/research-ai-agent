"""Sign up, log in, log out, the current user, the password and the profile (step 9d)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request, Response

from src.api.auth import (
    COOKIE_NAME,
    SESSION_DAYS,
    CurrentUser,
    clear_session_cookie,
    hash_password,
    new_token,
    set_session_cookie,
    token_hash,
    verify_password,
)
from src.api.deps import SessionDep
from src.api.errors import ApiError
from src.api.schemas.accounts import (
    LoginRequest,
    Me,
    PasswordChange,
    ProfileOut,
    ProfileUpdate,
    ResearchLimits,
    SignupRequest,
)
from src.api.schemas.common import ErrorResponse
from src.db.models import User
from src.db.repositories.users import (
    EmailTaken,
    create_session,
    create_user,
    delete_other_sessions,
    delete_session,
    find_user_by_email,
)
from src.timeutil import now

router = APIRouter(tags=["accounts"])
AUTH_ERRORS = {code: {"model": ErrorResponse} for code in (401, 409, 429)}


def me(user: User) -> Me:
    return Me(id=user.id, name=user.name, email=user.email, created_at=user.created_at)


async def log_in(session, request: Request, response: Response, user: User) -> None:
    token, moment = new_token(), now()
    await create_session(session, user_id=user.id, token_hash=token_hash(token),
                         expires_at=moment + timedelta(days=SESSION_DAYS), now=moment,
                         user_agent=request.headers.get("user-agent"))
    user.last_login_at = moment
    set_session_cookie(response, token)


@router.post("/auth/signup", status_code=201, response_model=Me, responses=AUTH_ERRORS)
async def signup(body: SignupRequest, request: Request, response: Response, session: SessionDep) -> Me:
    """Creates your account and logs you in."""
    try:
        user = await create_user(session, email=body.email, name=body.name, password_hash=hash_password(body.password))
    except EmailTaken:
        raise ApiError(409, "email_taken", "An account with this email already exists: log in instead.") from None
    await log_in(session, request, response, user)
    return me(user)


@router.post("/auth/login", response_model=Me, responses=AUTH_ERRORS)
async def login(body: LoginRequest, request: Request, response: Response, session: SessionDep) -> Me:
    """Logs you in. A wrong email and a wrong password get the same answer, so nobody can find out which emails
    have accounts; 5 failures in 15 minutes (per email or address) pause logins for a while."""
    limiter = request.app.state.login_limiter
    keys = (f"email:{body.email}", f"ip:{request.client.host if request.client else '?'}")
    limiter.check(*keys)
    user = await find_user_by_email(session, body.email)
    if user is None or not verify_password(user.password_hash, body.password):
        limiter.failed(*keys)
        raise ApiError(401, "wrong_login", "The email or the password is wrong.")
    limiter.succeeded(keys[0])
    await log_in(session, request, response, user)
    return me(user)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, session: SessionDep) -> Response:
    """Ends this session (its cookie stops working at once)."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await delete_session(session, token_hash(token))
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/auth/me", response_model=Me, responses=AUTH_ERRORS)
async def get_me(user: CurrentUser) -> Me:
    return me(user)


@router.post("/auth/password", status_code=204, responses=AUTH_ERRORS)
async def change_password(body: PasswordChange, request: Request, user: CurrentUser, session: SessionDep) -> Response:
    """Changes your password and logs out your other sessions (this one stays logged in)."""
    if not verify_password(user.password_hash, body.current):
        raise ApiError(401, "wrong_password", "Your current password is wrong.")
    user.password_hash = hash_password(body.new)
    token = request.cookies.get(COOKIE_NAME)
    await delete_other_sessions(session, user.id, token_hash(token) if token else None)
    await session.flush()
    return Response(status_code=204)


def profile_out(user: User) -> ProfileOut:
    return ProfileOut(name=user.name, email=user.email, limits=ResearchLimits.model_validate(user.profile or {}))


@router.get("/profile", response_model=ProfileOut, responses=AUTH_ERRORS)
async def get_profile(user: CurrentUser) -> ProfileOut:
    return profile_out(user)


@router.put("/profile", response_model=ProfileOut, responses=AUTH_ERRORS)
async def update_profile(body: ProfileUpdate, user: CurrentUser, session: SessionDep) -> ProfileOut:
    """Your name and research limits. New investigations copy the limits; existing ones keep the copy they made."""
    if body.name is not None:
        user.name = body.name
    user.profile = body.limits.model_dump(exclude_none=True)
    await session.flush()
    return profile_out(user)
