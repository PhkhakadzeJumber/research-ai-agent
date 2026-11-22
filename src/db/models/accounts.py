"""Login sessions (step 9d). ER region 1, next to User.

Logging in creates a UserSession and gives the browser a random token in an HttpOnly cookie. Only the token's
SHA-256 hash is stored here, so the table can't be used to log in as anyone. A session lasts 30 days and is
extended while it is used; logging out deletes it (the token stops working at once). Deleting a user deletes their
sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import AwareDateTime, Base, Timestamps, UUIDPrimaryKey


class UserSession(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    """SHA-256 of the cookie's token, hex."""
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(AwareDateTime())
    user_agent: Mapped[str | None] = mapped_column(String(200))
    """The browser, for a future "your sessions" list."""
