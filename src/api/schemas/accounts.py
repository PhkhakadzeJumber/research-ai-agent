"""Accounts and the profile (step 9d): sign up, log in, the current user, changing the password, and the research
limits (doc 10 §2.1) that every new investigation copies.

profile_constraints() turns the limits into the name -> text pairs investigations store and the agents read (e.g.
{"available time": "12 weeks, 10 hours per week", "compute": "free notebook GPU"}): the timeline check reads the one
whose name mentions time, pre-screening reads compute, and every agent sees all of them.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from src.api.auth import MAX_PASSWORD, MIN_PASSWORD
from src.api.schemas.billing import PlanInfo
from src.api.schemas.common import ApiModel, Timestamp

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
Password = Annotated[str, StringConstraints(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _WithEmail(_Request):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320)]

    @field_validator("email")
    @classmethod
    def _looks_like_an_email(cls, value: str) -> str:
        if not EMAIL.match(value):
            raise ValueError("not an email address")
        return value.lower()


class SignupRequest(_WithEmail):
    name: Name
    password: Password
    """At least 10 characters."""


class LoginRequest(_WithEmail):
    password: Annotated[str, StringConstraints(min_length=1, max_length=MAX_PASSWORD)]


class ResetRequest(_WithEmail):
    """Asks for a password-reset link (step B)."""


Token = Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=200)]


class EmailLink(_Request):
    """The token from a link in an email (step B)."""

    token: Token


class PasswordReset(_Request):
    token: Token
    password: Password
    """The new password: at least 10 characters."""


class PasswordChange(_Request):
    current: Annotated[str, StringConstraints(min_length=1, max_length=MAX_PASSWORD)]
    new: Password


class Me(ApiModel):
    id: uuid.UUID
    name: str | None
    email: str
    created_at: Timestamp
    spent_this_month_usd: float
    """What your investigations' Claude calls cost since the 1st of this month (Tbilisi time): the nav's chip."""
    plan: PlanInfo
    """The plan, credits and prices (step P2): the nav's chip and the start buttons."""
    email_verified: bool
    """Whether the email was verified (step B): a free account needs it before its free evaluation."""


AcademicLevel = Literal["undergraduate", "master", "phd", "researcher"]
ComputeTier = Literal["laptop", "free_notebook_gpu", "one_gpu", "cluster"]
DataAccess = Literal["public_only", "can_collect", "institutional"]
Budget = Literal["none", "small", "some"]
Supervision = Literal["yes", "limited", "no"]


class ResearchLimits(BaseModel):
    """Your research limits; every field is optional."""

    model_config = ConfigDict(extra="forbid")

    academic_level: AcademicLevel | None = None
    weeks_available: Annotated[float, Field(gt=0, le=520)] | None = None
    hours_per_week: Annotated[float, Field(gt=0, le=100)] | None = None
    compute: ComputeTier | None = None
    compute_note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    data_access: DataAccess | None = None
    budget: Budget | None = None
    """For paid tools, annotation or cloud costs."""
    skills: Note | None = None
    technologies: Note | None = None
    """Preferred technologies and subfields."""
    ethics: Note | None = None
    """What you can't or won't do, e.g. no human-subject data."""
    supervision: Supervision | None = None
    other: Note | None = None


class ProfileOut(ApiModel):
    name: str | None
    email: str
    limits: ResearchLimits


class ProfileUpdate(_Request):
    name: Name | None = None
    limits: ResearchLimits
    """Replaces your limits as a whole (leave a field out to clear it)."""


LABELS = {
    "compute": {"laptop": "laptop only", "free_notebook_gpu": "free notebook GPU (e.g. Colab)",
                "one_gpu": "one GPU for days", "cluster": "a cluster"},
    "data_access": {"public_only": "public data only", "can_collect": "can collect new data",
                    "institutional": "institutional data"},
    "academic_level": {"phd": "PhD"},
}


def profile_constraints(profile: dict[str, Any] | None) -> dict[str, str]:
    """The limits as the readable name -> text pairs an investigation stores (doc 10: a snapshot, per run)."""
    limits = ResearchLimits.model_validate(profile or {})
    out: dict[str, str] = {}
    if limits.academic_level:
        out["academic level"] = LABELS["academic_level"].get(limits.academic_level, limits.academic_level)
    if limits.weeks_available:
        out["available time"] = f"{limits.weeks_available:g} weeks" + (
            f", {limits.hours_per_week:g} hours per week" if limits.hours_per_week else "")
    elif limits.hours_per_week:
        out["hours per week"] = f"{limits.hours_per_week:g}"
    if limits.compute or limits.compute_note:
        label = LABELS["compute"].get(limits.compute, "") if limits.compute else ""
        out["compute"] = (f"{label} ({limits.compute_note})" if label and limits.compute_note
                          else label or limits.compute_note)
    if limits.data_access:
        out["data access"] = LABELS["data_access"][limits.data_access]
    for key, name in (("budget", "budget for paid tools"), ("skills", "skills"),
                      ("technologies", "preferred technologies"), ("ethics", "ethics limits"),
                      ("supervision", "supervision"), ("other", "other")):
        value = getattr(limits, key)
        if value:
            out[name] = value
    return out
