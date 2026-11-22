"""Write the research package for an evaluated idea (agent A7; doc 02 §5; step 7).

Usage (from the repo root, venv active, database running, Anthropic key in .env):
    python -m scripts.proposal <investigation id>                       the top-ranked finalist
    python -m scripts.proposal <investigation id> --candidate <id>      a chosen finalist
    python -m scripts.proposal <investigation id> --out package.md      also save it as Markdown
    python -m scripts.proposal <investigation id> --show                the latest stored package (free)

The investigation must be completed (results ready). The Proposal Designer writes the package's written sections
in one call (about 25-40 cents); code checks every statement's sources and evidence status and renders the other
sections from the evaluation. The 17 sections are printed with their evidence status: evidence (with sources),
interpretation, hypothesis or "to verify".
Exit code: 0 ok, 1 the Proposal Designer failed, 2 invalid input or an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from src.ai.workflows.proposal import NoCandidateForProposal, package_sections, write_proposal
from src.ai.workflows.transitions import StaleState
from src.config import ConfigError
from src.db.models import Candidate, ResearchPackage
from src.db.session import Database
from src.llm import LlmError


def to_markdown(candidate: Candidate, package: ResearchPackage, sections: list[dict]) -> str:
    lines = [f"# Research package: {sections[0]['items'][0]}", "",
             f"Candidate `{candidate.id}` | package revision {package.revision} | grounded in evaluation "
             f"`{package.evaluation_id}` | {package.prompt_version}"]
    checks = package.sections.get("checks") or {}
    if checks.get("retagged") or checks.get("dropped_labels"):
        lines.append(f"Code's checks: {checks.get('retagged', 0)} statement(s) re-tagged 'to verify' (no verified "
                     f"source), {checks.get('dropped_labels', 0)} unknown source label(s) dropped.")
    for section in sections[1:]:
        lines += ["", f"## {section['number']}. {section['title']}", f"*{section['status']}*", ""]
        items = [item for item in section["items"] if item]
        lines.extend(f"- {item}" for item in items) if items else lines.append("- (nothing here)")
    lines += ["", "*Statements marked 'to verify' are needed by the plan but not backed by the evidence Eureka found: "
                  "check them before relying on them.*"]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.proposal", description="Write a research package.")
    parser.add_argument("investigation", type=uuid.UUID)
    parser.add_argument("--candidate", type=uuid.UUID, help="a finalist's ID (default: the top-ranked finalist)")
    parser.add_argument("--out", type=Path, help="also save the package as a Markdown file")
    parser.add_argument("--show", action="store_true", help="print the latest stored package instead (no Claude call)")
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
        if args.show:
            async with database.session() as session:
                query = select(ResearchPackage).join(Candidate).where(Candidate.investigation_id == args.investigation)
                if args.candidate:
                    query = query.where(Candidate.id == args.candidate)
                package = (await session.execute(query.order_by(ResearchPackage.created_at.desc()).limit(1))
                           ).scalar_one_or_none()
                if package is None:
                    print("No research package yet: run without --show to write one.", file=sys.stderr)
                    return 2
                candidate = await session.get(Candidate, package.candidate_id)
                text = to_markdown(candidate, package, await package_sections(session, package))
        else:
            print("Writing the research package (Proposal Designer, one call)...", file=sys.stderr)
            try:
                outcome = await write_proposal(database.session, args.investigation, candidate_id=args.candidate)
            except (NoCandidateForProposal, StaleState) as error:
                print(f"Error: {error}", file=sys.stderr)
                return 2
            except LlmError as error:
                print(f"The Proposal Designer failed: {error}. Nothing was stored; run it again.", file=sys.stderr)
                return 1
            async with database.session() as session:
                package = await session.get(ResearchPackage, outcome.package.id)
                text = to_markdown(outcome.candidate, package, await package_sections(session, package))
        print(text)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
            print(f"\n(written to {args.out})", file=sys.stderr)
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
