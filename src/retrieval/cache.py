"""Response cache for literature sources: repeated requests don't hit the APIs again.

Why: OpenAlex charges each request against a daily budget, arXiv asks for slow request
rates, and tests/demos should be repeatable. A cached response is reused for 7 days.

FileResponseCache stores files in `.cache/http/` (gitignored); DbResponseCache
(src/db/repositories/cache.py) stores rows in the api_cache table. Both follow the
ResponseCache interface, so the source adapters don't care which one is used.

Secrets never reach the cache: parameters like `api_key` and `mailto` are left out of
both the cache key and the stored file. The same query with a different key is the
same cached response.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from src import timeutil

logger = logging.getLogger(__name__)

DEFAULT_TTL = timedelta(days=7)
SECRET_PARAMS = frozenset({"api_key", "key", "token", "mailto", "email"})


class ResponseCache(Protocol):
    """What SourceHttpClient needs from a cache: FileResponseCache (files) or
    DbResponseCache (the api_cache table, src/db/repositories/cache.py).
    Async, because database access is async."""

    async def get(self, source: str, path: str, params: Mapping[str, Any]) -> str | None: ...

    async def put(self, source: str, path: str, params: Mapping[str, Any], text: str) -> None: ...


def public_params(params: Mapping[str, Any]) -> dict[str, str]:
    """The request parameters without secrets, sorted, as strings."""
    return {name: str(value) for name, value in sorted(params.items()) if name.lower() not in SECRET_PARAMS}


def cache_key(source: str, path: str, params: Mapping[str, Any]) -> str:
    """A stable fingerprint of a request (source + path + non-secret parameters)."""
    description = json.dumps([source, path, public_params(params)], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


class FileResponseCache:
    """Stores each response as one JSON file: <directory>/<source>/<key>.json."""

    def __init__(
        self,
        directory: Path | str,
        *,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] = timeutil.now,
    ) -> None:
        self.directory = Path(directory)
        self.ttl = ttl
        self._clock = clock
        self.hits = 0
        self.misses = 0

    def _file(self, source: str, path: str, params: Mapping[str, Any]) -> Path:
        return self.directory / source / f"{cache_key(source, path, params)}.json"

    async def get(self, source: str, path: str, params: Mapping[str, Any]) -> str | None:
        """The cached response text, or None if missing, expired or unreadable."""
        file = self._file(source, path, params)
        if not file.exists():
            self.misses += 1
            return None
        try:
            entry = json.loads(file.read_text(encoding="utf-8"))
            fetched_at = timeutil.from_iso(entry["fetched_at"])
            text = entry["text"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.warning("cache: unreadable entry %s (%s); fetching again", file.name, error)
            self.misses += 1
            return None
        if self._clock() - fetched_at > self.ttl:
            self.misses += 1
            return None
        self.hits += 1
        return text

    async def put(self, source: str, path: str, params: Mapping[str, Any], text: str) -> None:
        """Stores a response. A failure to write is logged, never raised: caching is optional."""
        file = self._file(source, path, params)
        entry = {
            "source": source,
            "path": path,
            "params": public_params(params),
            "fetched_at": timeutil.to_iso(self._clock()),
            "text": text,
        }
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            temporary = file.with_suffix(".tmp")
            temporary.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, file)  # atomic: a reader never sees a half-written file
        except OSError as error:
            logger.warning("cache: could not write %s (%s)", file, error)
