"""Check that the databases are reachable and pgvector works.

Usage (from the repo root, with the venv active and `docker compose up -d db` running):
    python -m scripts.db_check

Checks both the main database and the test database. Prints the connection
addresses with the password hidden. Exit code 0 if both are fine, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import ConfigError, get_database_settings

CHECKS = {
    "server": "SELECT current_setting('server_version')",
    "pgvector": "SELECT extversion FROM pg_extension WHERE extname = 'vector'",
    # distance between two small vectors: proves vector maths works, not just that the extension exists
    "vector math": "SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector",
}

HINTS = (
    "Is Docker Desktop running, and was the database started with `docker compose up -d db`?\n"
    "  `docker compose ps` should show eureka-db as healthy.\n"
    "If the password was changed after the first start: the database keeps the first one.\n"
    "  Reset with `docker compose down -v` (deletes all data), then `docker compose up -d db`."
)


async def check(label: str, url: str, redacted: str) -> bool:
    print(f"{label}: {redacted}")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            for name, query in CHECKS.items():
                result = (await connection.execute(text(query))).scalar()
                if result is None:
                    print(f"  {name}: MISSING")
                    return False
                print(f"  {name}: {result}")
        return True
    except Exception as error:  # report any connection problem in plain words
        print(f"  FAILED: {type(error).__name__}: {error}".splitlines()[0])
        return False
    finally:
        await engine.dispose()


async def run() -> int:
    try:
        settings = get_database_settings()
    except ConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    ok_main = await check("main database", settings.url, settings.redacted())
    ok_test = await check("test database", settings.test_url, settings.redacted(test=True))
    if ok_main and ok_test:
        print("All good.")
        return 0
    print("\n" + HINTS)
    return 1


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
