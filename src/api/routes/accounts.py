"""Sign up, log in, log out, the current user, the password and the profile (step 9d); verifying the email and
resetting a forgotten password by emailed links (step B)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, func, select

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
from src.api.routes.billing import plan_info
from src.api.errors import ApiError
from src.api.schemas.accounts import (
    EmailLink,
    LoginRequest,
    PasswordReset,
    ResetRequest,
    Me,
    PasswordChange,
    ProfileOut,
    ProfileUpdate,
    ResearchLimits,
    SignupRequest,
)
from src.api.schemas.common import ErrorResponse
from src.api import email as mail
from src.config import get_email_settings
from src.db.models import EmailToken, Investigation, LlmCall, User
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


async def me(session, user: User) -> Me:
    month_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)  # Tbilisi time
    spent = (await session.execute(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0))
        .join(Investigation, Investigation.id == LlmCall.investigation_id)
        .where(Investigation.user_id == user.id, LlmCall.created_at >= month_start))).scalar_one()
    return Me(id=user.id, name=user.name, email=user.email, created_at=user.created_at,
              spent_this_month_usd=float(spent), plan=await plan_info(session, user),
              email_verified=user.email_verified_at is not None)


# --- links sent by email (step B) ---------------------------------------------------------------------------

LINK_HOURS = {"verify": 24, "reset": 1}
RESEND_AFTER = timedelta(seconds=60)
"""A new link of the same kind is sent at most once a minute (the inbox, and Resend's free quota, aren't flooded)."""


async def issue_link(session, user: User, purpose: str) -> str | None:
    """A new link for `purpose`, replacing the user's older ones; None if one was sent less than a minute ago."""
    moment = now()
    last = (await session.execute(select(func.max(EmailToken.created_at)).where(
        EmailToken.user_id == user.id, EmailToken.purpose == purpose))).scalar_one()
    if last is not None and moment - last < RESEND_AFTER:
        return None
    await session.execute(delete(EmailToken).where(EmailToken.user_id == user.id, EmailToken.purpose == purpose))
    token = new_token()
    session.add(EmailToken(user_id=user.id, purpose=purpose, token_hash=token_hash(token),
                           expires_at=moment + timedelta(hours=LINK_HOURS[purpose]), created_at=moment))
    await session.flush()
    page = "verify-email" if purpose == "verify" else "reset-password"
    return f"{get_email_settings().site_url}/{page}?token={token}"


async def use_link(session, token: str, purpose: str) -> User:
    """The user a link is for, marking the link used; a 400 if it is unknown, used or expired."""
    row = (await session.execute(select(EmailToken).where(
        EmailToken.token_hash == token_hash(token), EmailToken.purpose == purpose).with_for_update())).scalar_one_or_none()
    if row is None or row.used_at is not None:
        raise ApiError(400, "link_invalid", "This link isn't valid any more: it was used, or a newer one was sent.")
    if row.expires_at <= now():
        raise ApiError(400, "link_expired", "This link has expired: ask for a new one.")
    row.used_at = now()
    return await session.get(User, row.user_id)


async def send_verification(session, user: User) -> bool:
    link = await issue_link(session, user, "verify")
    return link is not None and await mail.send(mail.verification_email(user.email, user.name, link))


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
    await send_verification(session, user)  # a failed email doesn't fail the signup: "Resend the link" is there
    return await me(session, user)


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
    return await me(session, user)


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
async def get_me(user: CurrentUser, session: SessionDep) -> Me:
    return await me(session, user)


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


# --- verifying the email, resetting the password (step B) ------------------------------------------------------

@router.post("/auth/verify-email/send", status_code=204, responses=AUTH_ERRORS)
async def resend_verification(user: CurrentUser, session: SessionDep) -> Response:
    """Sends a new verification link (the older one stops working); at most one a minute."""
    if user.email_verified_at is not None:
        raise ApiError(409, "already_verified", "Your email is already verified.")
    link = await issue_link(session, user, "verify")
    if link is None:
        raise ApiError(429, "too_soon", "A link was just sent: check your inbox (and spam), or try again in a minute.")
    if not await mail.send(mail.verification_email(user.email, user.name, link)):
        raise ApiError(502, "email_failed", "The email couldn't be sent just now: try again in a minute.")
    return Response(status_code=204)


@router.post("/auth/verify-email", status_code=204, responses=AUTH_ERRORS)
async def verify_email(body: EmailLink, session: SessionDep) -> Response:
    """Verifies the email from the link (no login needed: the link may be opened on another device)."""
    user = await use_link(session, body.token, "verify")
    user.email_verified_at = user.email_verified_at or now()
    await session.flush()
    return Response(status_code=204)


@router.post("/auth/password-reset/request", status_code=202, responses=AUTH_ERRORS)
async def request_password_reset(body: ResetRequest, session: SessionDep) -> Response:
    """Emails a reset link if the email has an account. The answer is the same either way, so nobody can find out
    which emails have accounts."""
    user = await find_user_by_email(session, body.email)
    if user is not None:
        link = await issue_link(session, user, "reset")
        if link is not None:
            await mail.send(mail.reset_email(user.email, user.name, link))
    return Response(status_code=202)


@router.post("/auth/password-reset", response_model=Me, responses=AUTH_ERRORS)
async def reset_password(body: PasswordReset, request: Request, response: Response, session: SessionDep) -> Me:
    """Sets the new password from the link, logs out every session, and logs you in here. Opening the emailed link
    also proves the email is yours, so it counts as verified."""
    user = await use_link(session, body.token, "reset")
    user.password_hash = hash_password(body.password)
    user.email_verified_at = user.email_verified_at or now()
    await delete_other_sessions(session, user.id, None)
    await log_in(session, request, response, user)
    return await me(session, user)
