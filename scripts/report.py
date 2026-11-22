"""The full report of an investigation (both workflows), built only from what is stored.

Usage (from the repo root, venv active, database running):
    python -m scripts.report <investigation id>
    python -m scripts.report <investigation id> --out report.md

No Claude calls: everything comes from the database (frozen verdicts if an evaluation has
concluded, otherwise the current ones, marked preliminary). Paper titles and links are
rendered from the stored records, never written by a model. Re-run it any time.

Mode 1 (evaluate an idea): the idea, its verdicts and evidence, then any refined versions and
alternatives. Mode 2 (find ideas, step 5.6b): the request, the field brief, the landscape and the
open problems mined from it, the ranked finalists (each with the same per-idea section as Mode 1),
the ideas that passed screening without a full evaluation, and every other idea with what happened
and why. The ranking is computed from the stored verdicts (src/ai/workflows/ranking.py).
Exit code: 0 ok, 2 unknown investigation or an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from src.ai.citations import paper_link  # noqa: F401  (kept importable from here)
from src.ai.reporting import build_report, document_lines, document_vs_literature  # noqa: F401  (moved; importable)
from src.config import ConfigError
from src.db.session import Database


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.report", description="Print an investigation's report.")
    parser.add_argument("investigation", type=uuid.UUID)
    parser.add_argument("--out", type=Path, help="also write the report to this Markdown file")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    try:
        database = Database.from_settings()
    except ConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    try:
        if not await database.ping():
            print("Error: the database isn't reachable: start it with `docker compose up -d db`.", file=sys.stderr)
            return 2
        async with database.session() as session:
            try:
                report = await build_report(session, args.investigation)
            except LookupError as error:
                print(f"Error: {error}", file=sys.stderr)
                return 2
        print(report)
        if args.out:
            args.out.write_text(report + "\n", encoding="utf-8")
            print(f"\n(written to {args.out})")
        return 0
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
