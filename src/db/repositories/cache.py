"""DbResponseCache: the literature-API response cache stored in the api_cache table.

Same behavior as FileResponseCache (src/retrieval/cache.py), same ResponseCache
interface, so source adapters can use either:
  - responses are reused for 7 days (TTL);
  - secrets (api_key, mailto, ...) are never part of the key or the stored parameters;
  - a database problem never breaks a search: it counts as a cache miss / is logged.

Each get/put uses its own short session from `sessions` (e.g. Database.session), so
caching never interferes with the caller's own transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src import timeutil
from src.db.models import ApiCache
from src.retrieval.cache import DEFAULT_TTL, cache_key, public_params

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class DbResponseCache:
    def __init__(
        self,
        sessions: SessionFactory,
        *,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] = timeutil.now,
    ) -> None:
        self._sessions = sessions
        self.ttl = ttl
        self._clock = clock
        self.hits = 0
        self.misses = 0

    async def get(self, source: str, path: str, params: Mapping[str, Any]) -> str | None:
        """The cached response text, or None if missing, expired or unreadable."""
        try:
            async with self._sessions() as session:
                entry = await session.get(ApiCache, cache_key(source, path, params))
                if entry is None or self._clock() - entry.fetched_at > self.ttl:
                    self.misses += 1
                    return None
                self.hits += 1
                return entry.response
        except SQLAlchemyError as error:
            logger.warning("db cache: read failed for source=%s (%s); fetching again", source, type(error).__name__)
            self.misses += 1
            return None

    async def put(self, source: str, path: str, params: Mapping[str, Any], text: str) -> None:
        """Stores (or refreshes) a response. Failures are logged, never raised: caching is optional."""
        values = dict(
            key=cache_key(source, path, params), source=source, path=path,
            params=public_params(params), response=text, fetched_at=self._clock(),
        )
        statement = insert(ApiCache).values(**values).on_conflict_do_update(
            index_elements=[ApiCache.key],
            set_={"params": values["params"], "response": text, "fetched_at": values["fetched_at"]},
        )
        try:
            async with self._sessions() as session:
                await session.execute(statement)
        except SQLAlchemyError as error:
            logger.warning("db cache: write failed for source=%s (%s)", source, type(error).__name__)
