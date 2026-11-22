# 11: Backend and API

## 1. Technology choices

| Concern | Choice | Alternatives considered |
|---|---|---|
| Language | Python 3.11+ | — |
| Web framework | FastAPI | Django (heavier, not needed) |
| Agents | Google ADK + Anthropic model class | LangGraph (alternative); plain SDK inside services where needed |
| Database | PostgreSQL 16 + pgvector; SQLAlchemy 2 + Alembic migrations | Firestore (bad fit for relational evidence) |
| HTTP client | Async client with retry and rate-limiting helpers | Synchronous requests (blocks concurrency) |
| Background jobs | MVP: async background task in the API process, with all state in the DB (resumable). Later: Cloud Tasks or Cloud Run Jobs | Celery + Redis (overkill) |
| Progress streaming | Server-Sent Events (SSE) | WebSockets (two-way not needed) |
| PDF parsing | A PDF text extraction library; GROBID later | — |
| Config | Environment variables + versioned config files (models per role, budgets, rule tables, source tiers) | — |

## 2. Repository layout

All Python code is **one package, `src/`**, for one service. Inside it, each folder has one responsibility, and AI engineering is kept apart from web plumbing.

```text
research-ai-agent/
  README.md · CLAUDE.md · pyproject.toml · requirements.txt · .env.example
  compose.yaml               # local Postgres 17 + pgvector (Docker)
  docs/                      # these documents
  src/                       # all Python code
    config.py                # settings from env vars (read once)
    llm.py                   # the single gateway to Claude (all model calls)
    ai/                      # AI engineering
      agents/                # A1–A7, one file per agent (doc 04)
      prompts/               # versioned prompt files, one per agent (doc 08)
      schemas/               # agent output shapes: PaperAnalysis, NoveltyAssessment, … (doc 10 §12)
      tools/                 # agent tools: search_papers, get_passages, … (doc 09)
      workflows/             # orchestrator: Workflow A/B, triage rules, budgets (doc 05)
      evidence/              # quote verifier, verdict engine, coverage snapshot (doc 07)
    retrieval/               # RAG / data gathering (doc 06)
      records.py             # normalized paper record + search request/result shapes
      sources/               # Semantic Scholar, OpenAlex, arXiv, Crossref adapters
      signals/               # GitHub, Hugging Face adapters (tier 3)
      fulltext/              # PDF fetching, parsing, chunking
      embeddings.py · ranking.py · dedup.py
      mining/                # limitation clusters, contradictions, method × problem matrix
    db/                      # data layer
      models/                # SQLAlchemy tables, grouped by the six ER regions (er-model.md)
      repositories/          # read/write functions per region
      migrations/            # Alembic
    api/                     # web backend
      main.py                # FastAPI app
      routes/                # investigations, candidates, papers, export (section 3)
      schemas/               # API request/response models
      events.py              # progress streaming (SSE)
  tests/                     # mirrors src/ (tests/retrieval/, tests/ai/, …) + fixtures/ (recorded responses)
  eval/                      # evaluation harness, datasets, baselines, reports (doc 13)
  frontend/                  # Next.js app, frontend only (doc 12)
  infra/                     # db/init/ (first-start SQL), Dockerfiles, deploy scripts (doc 14)
  scripts/                   # small command-line helpers (e.g. run a search)
```

**Dependency rules:**

- Imports only go downward: `api → ai → retrieval / db → config / llm`. Nothing imports a layer above it, so the AI part runs without the website (from scripts, tests and the evaluation harness).
- Agents use retrieval only through `ai/tools/` or the workflows' context building.
- The frontend talks to the backend only over HTTP.
- **Three kinds of shapes stay separate**, even when they look alike:
  - agent output shapes (`ai/schemas/`)
  - database tables (`db/models/`)
  - API responses (`api/schemas/`)

  The LLM fills agent shapes, code adds IDs and trust fields, and the API decides what the user sees.

Folders are created when their first file is written, not in advance.

## 3. API

All routes are under `/api/v1`. They require authentication, except health checks (from step 9d; until then the API runs on your own machine only).

**Built so far (steps 9a-9b):** the routes marked ✅ below, in `src/api/`. Every response has its own Pydantic model (`src/api/schemas/`); timestamps are ISO with the +04:00 offset; every category comes with its label (`{"category": "potential_opportunity", "label": "Potential opportunity"}`); lists are `{"items": [...], "total": N}`; errors are `{"error": {"code", "message", "request_id"}}`, and every response carries an `X-Request-ID` header.

### 3.1 Investigations (both modes)

| Method | Route | Purpose |
|---|---|---|
| POST | `/investigations` ✅ | Create (JSON). Mode 1: idea, field, seed papers, complexity, constraints, budget. Mode 2: field, interests, technologies, domain, exclusions, count, complexity, constraints, budget, light-evaluation cap. Eureka's first reading runs in the background; `202` with the investigation |
| POST | `/investigations/document` ✅ | Create Mode 1 from the user's own document (multipart upload: .pdf/.md/.txt, `consent=true` required, optional published/DOI/arXiv). Nothing is stored without consent |
| GET | `/investigations` ✅ | List the user's investigations |
| GET | `/investigations/{id}` ✅ | Status, stage, mode, summary, budgets and counters |
| GET | `/investigations/{id}/clarification` ✅ | The structured reading (facets or field brief) + questions |
| POST | `/investigations/{id}/clarification` ✅ | Answers (null skips), facet edits (Mode 1) or subtopics to remove (Mode 2), `confirm: true` → starts the main run in the background |
| GET | `/investigations/{id}/events` | **SSE** stream of progress events |
| POST | `/investigations/{id}/stop` ✅ | Stop at the next step boundary. A pause, not a final cancel: nothing is lost and it can be resumed |
| POST | `/investigations/{id}/resume` ✅ | Continue where it stopped (after Stop, the budget, an account refusal or a server restart), optionally with a new budget |
| GET | `/investigations/{id}/coverage` | Investigation-level coverage summary |
| GET | `/investigations/{id}/results` ✅ | The ranked results: finalists (with why each comes before the next), ideas that passed screening, and everything set aside with its reason |
| GET | `/investigations/{id}/cost` ✅ | Cost per agent role and in total, against the budget |
| GET | `/investigations/{id}/landscape` ✅ | Mode 2: subtopic map, limitation clusters, contradictions, combination hints |
| GET | `/investigations/{id}/signals` | Mode 2: tier-3 signals used |

### 3.2 Candidates

| Method | Route | Purpose |
|---|---|---|
| GET | `/investigations/{id}/candidates` | All candidates with status, verdict summaries, rejection reasons. Filters: status, origin |
| GET | `/candidates/{cid}` ✅ | Full candidate: facets, motivation, lineage, evaluations, verdicts, triage history |
| GET | `/candidates/{cid}/matrix` ✅ | Facet × paper matrix |
| GET | `/candidates/{cid}/claims` ✅ | Claims with evidence (filter by kind or status) |
| GET | `/candidates/{cid}/challenges` ✅ | Skeptic log |
| GET | `/candidates/{cid}/coverage` | Coverage report |
| GET | `/candidates/compare?ids=…` | Side-by-side comparison data |
| POST | `/candidates/{cid}/steer` | Mode 2 steering: more like this / simpler / exclude X / combine with Y → a Scout task |
| POST | `/candidates/{cid}/deep-dive` | Run Workflow A (full evaluation) on this candidate |
| POST | `/candidates/{cid}/proposal` ✅ | Generate the research package in the background (a new revision each time) |
| GET | `/candidates/{cid}/proposal` ✅ | Get the research package |
| GET | `/candidates/{cid}/graph` | Evidence graph nodes and edges |

### 3.3 Papers, export, conversation, profile

| Method | Route | Purpose |
|---|---|---|
| GET | `/papers` ✅ | The shared paper library across investigations: search titles and abstracts (`q`), filter read closely / found in your investigations / year. (Per-investigation filtering can come later) |
| GET | `/papers/{pid}` ✅ | Metadata + analysis + quotes used |
| GET | `/investigations/{id}/report?format=md` ✅ | Investigation report as Markdown (PDF later) |
| GET | `/candidates/{cid}/proposal/export?format=md` ✅ | Package export as Markdown (PDF, docx later) |
| POST | `/investigations/{id}/messages` | "Ask about this investigation" (optional feature) |
| GET/PUT | `/profile` | Constraints profile |
| GET | `/health`, `/ready` | Health checks |

### 3.4 Progress events ✅ (step 9c)

| Method | Route | Purpose |
|---|---|---|
| GET | `/investigations/{id}/events` ✅ | **SSE stream**: stored events after `?after=` (or the browser's `Last-Event-ID`), then new ones as the run stores them; a comment line every 15 s keeps proxies from closing it; it ends after the run's `ended` event, or right after the replay if nothing runs |
| GET | `/investigations/{id}/activity?after=&limit=` ✅ | The same events as a JSON list (first page load, the activity feed) |
| GET | `/investigations/{id}/progress` ✅ | The progress screen at once: the stage rail for the mode (each stage done / active / paused / failed / to do / skipped), counters from the database, cost against budget, run state, how the last run ended |

Each event (SSE `id` = its sequence number, SSE `event` = its kind) carries: `seq`, `kind` (stage / step_started / step_done / candidate / warning / error / ended), `stage`, `step`, a human-readable `message`, `data` (the numbers behind it; for `ended`, the reason: done / stopped / budget / account_refused / failed / nothing), `candidate_id`, `cost_usd` (spend at that moment) and `at`.

Events are stored (doc 10 §3.3) by the driver's recorder (`src/ai/progress.py`) for runs started from the API **and** from the command line, so a reconnecting client can replay them and a command-line run shows on the website too. `GET /investigations/{id}` carries `last_run` (from the last `ended` event).

## 4. Background execution

- Creating, clarifying, resuming or writing a package starts a background task in the API process (`src/api/runs.py`), and the request returns `202` at once. The investigation's `run` field says `running` / `stopping` / `idle`.
- The run control is the driver (`src/ai/workflows/driver.py`, step 9b.1): it carries an investigation from its stage until it needs the user, ends, runs out of budget, is refused by Anthropic or is asked to stop. The command-line scripts use the same driver and print its events.
- State is saved after every step. **Runs are not resumed automatically when the server starts** (a restart would quietly spend money): an interrupted run shows as idle and the user resumes it (`POST /resume`).
- Stop is a pause (checked before every step); the final `cancelled` status is not used by the API.
- Limits: one run per investigation at a time (`409` otherwise); at most 2 runs at once per process (the rest wait); a budget ceiling per investigation (`EUREKA_MAX_BUDGET_USD`, default $20).
- **Cloud Run note:** background work after the response needs CPU that stays allocated (instance-based billing or "CPU always allocated") and a minimum instance count of 1 during demos. Otherwise background tasks get throttled. Moving runs to Cloud Run Jobs/Tasks later removes this constraint.

## 5. Error handling summary

| Layer | Strategy |
|---|---|
| Source adapters | Retry with backoff → skip the source → record it in the coverage report |
| LLM calls | SDK retries → stage retry → partial stage (doc 08) |
| Verifier failures | Downgrade claims; never block the run |
| Orchestrator | Catch per stage; mark the stage failed/partial; continue where the rules allow; mark the investigation failed only if a mandatory stage (intake, any retrieval at all) fails |
| API | One error shape with a request ID (also in the `X-Request-ID` header); no stack traces to clients |

## 6. Observability

- **Structured JSON logs** with the investigation ID, candidate ID, stage, agent and correlation ID → Cloud Logging.
- **LLM and tool call tables** (doc 10) for cost and latency analysis.
- **Metrics:** investigations per day, average cost per mode, stage durations, source error rates, verifier failure rates, hallucinated-ID counts.
- Optional OpenTelemetry tracing later.

## 7. Security

- Keys only in environment variables / Secret Manager. Never in the repo, the image or the frontend.
- Simple authentication for the demo (Firebase Auth or Google IAP in front of Cloud Run).
- Per-user rate limits and per-investigation cost caps, so a public URL can't drain the Anthropic budget.
- Retrieved text is treated as untrusted (doc 08). Agents have no dangerous tools.
- CORS limited to the frontend origin.
- Never log full prompts containing user input in production logs; store them in the call tables with access control.
