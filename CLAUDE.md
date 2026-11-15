# CLAUDE.md

Eureka: an AI research discovery & validation agent with two modes (evaluate an idea / find an idea). Solo project. The design lives in `docs/` (reading order in `README.md`); the data model in `docs/10-data-model.md` and `docs/er-model.md` is the contract every agent writes to.

Stack: Python 3.11 · FastAPI · Google ADK with Claude (`AnthropicLlm`) · PostgreSQL + pgvector · Next.js (frontend only).

## Commands

Commands marked *(planned)* don't exist yet. When you add one, update this section in the same change.

```bash
# Python environment (Git Bash on Windows; PowerShell: .venv\Scripts\Activate.ps1)
python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements.txt && pip install -e ".[dev]"   # exact pinned versions, then the app + dev tools
pip freeze --exclude-editable > requirements.txt             # after changing dependencies in pyproject.toml (keep the header)

# Database: Postgres 17 + pgvector in Docker (compose.yaml; needs Docker Desktop running and POSTGRES_PASSWORD in .env)
docker compose up -d db                      # start; port 5434 on localhost only
docker compose ps                            # should show eureka-db as healthy
python -m scripts.db_check                   # checks main + test database and pgvector
alembic upgrade head                         # apply migrations (URL from settings, never in alembic.ini)
alembic revision --autogenerate -m "what changed"  # draft a migration after changing models; review it before use
alembic current                              # which migration the database is at

# Literature search from the command line (responses cached 7 days: database api_cache, or .cache/http/ if the DB is down)
python -m scripts.search "your query" --sources arxiv     # arXiv only: free
python -m scripts.search "your query" --limit 10 --from 2022  # all sources: uses a little OpenAlex budget, ask first
python -m scripts.search "your query" --sources arxiv --save  # store as an investigation (needs the database)
python -m scripts.search "your query" --sources arxiv --save --rank  # + rank by embeddings (needs Vertex AI setup)
python -m scripts.read_papers --latest --top 5 [--details]  # Paper Reader on a saved search: paid API, ask first

# Backend
uvicorn src.api.main:app --reload            # (planned) API on http://127.0.0.1:8000

# Verify your work
pytest                                       # default run: no network, no paid API calls ("live" tests excluded);
                                             # "db" tests use eureka_test, roll back, and are skipped (with reason) if the DB is down
pytest -m live                               # calls Claude/real APIs: costs money, ask first
ruff check . && ruff format --check .        # (planned)

# Frontend
cd frontend && npm install && npm run dev    # (planned) http://localhost:3000
cd frontend && npm run lint && npm run build # (planned)
```

## Conventions

- **Layout** (full tree in `docs/11-backend-and-api.md` §2): one Python package `src/`, with `config.py` + `llm.py` at the top, and these areas:
  - `ai/`: agents, prompts (versioned files), schemas, tools, workflows, evidence
  - `retrieval/`: sources, dedup, ranking, full text, embeddings, mining
  - `db/`: models, repositories, migrations
  - `api/`: FastAPI

  Outside `src/`: `tests/` (mirrors `src/`, with `fixtures/` for recorded responses), `eval/`, `frontend/` (Next.js), `infra/`, `scripts/`, `docs/`. Create folders when their first file is written.
- **Imports only go downward:** `api → ai → retrieval/db → config/llm`. Agent output shapes (`ai/schemas`), DB tables (`db/models`) and API responses (`api/schemas`) are separate types.
- **Model IDs come from env vars** (names listed in `.env.example`), read once in `src/config.py`. Never hardcode a model ID.
- **All Claude calls go through `src/llm.py`**: it builds the ADK `AnthropicLlm` models per role, makes any direct SDK calls, and logs tokens/cost/latency. No other module imports `anthropic`. Pass `AnthropicLlm(...)` objects, never a bare `"claude-..."` string (ADK routes those to Vertex AI).
- **Structured outputs need adaptive thinking** in ADK (`thinking_budget=-1`), otherwise Claude rejects the forced tool call.
- **Timestamps:** ISO 8601 with offset, Asia/Tbilisi, e.g. `2026-11-14T18:30:00+04:00`. Store as `timestamptz`; never naive datetimes.
- **Every agent output is a Pydantic model** matching the shapes in doc 10; every API response has a FastAPI response model.
- **Trust fields are code-only:** an LLM never sets quote verification, claim status, verdicts, tiers or source levels.
- **Papers are referenced by internal ID only.** Titles/DOIs in output are rendered from the database, never written by an LLM.
- **External APIs** go through adapters with timeouts, retries with backoff, rate limits and the API cache. Tools return errors as data (`status` + `error_message`).
- **Tests:** the default `pytest` run uses recorded fixtures (no network). Tests that hit real APIs are marked `live`.
- **Docs stay true:** if code changes the design, update the relevant doc in the same change.
- **Commits:** in the user's name only. A short subject line, a blank line, then a description: what changed and why (key files, decisions, test status). No `Co-Authored-By`, no mention of Claude. Commit only when asked.

## Always

- Run `pytest` before saying you're done, and paste the result (summary line + any failures).
- Run `ruff check .` too once it's set up.
- Briefly explain each new file: what it is and why it exists. The user is learning the codebase.
- When adding an env var, add its **name** (no value) to `.env.example`.

## Ask first

- Adding a dependency (Python or npm).
- Changing a response shape: API response models, agent output schemas, or the database schema (migrations).
- Editing `.github/workflows/`.
- Running anything that calls paid APIs: `pytest -m live`, evaluation runs, scripts that call Claude.
- `git push`, and any history rewrite.

## Never

- Read or write `.env`, or print a key or secret (in output, logs, tests or errors).
- Delete, skip or weaken a test to make it pass. Fix the code, or explain why the test is wrong and ask.
- Modify the course repo `../ai-llm-course` (read-only reference).
- Hardcode model IDs, API keys or URLs that belong in config.
- Include yourself in any commit or push. That means no `Co-Authored-By: Claude` trailer, no "Generated with Claude Code" line, no Claude/AI mention in commit messages or PR descriptions, and no changes to the git author. Every commit is authored only by the user's own git identity.
