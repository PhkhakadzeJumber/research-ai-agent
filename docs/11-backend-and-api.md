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

```text
research-ai-agent/
  README.md
  docs/                      # these documents
  .env.example               # key names only, no values
  backend/
    app/
      api/                   # HTTP routes (investigations, candidates, papers, export, conversation)
      orchestrator/          # state machines for Workflow A and B, triage rules, budgets
      agents/                # A1–A7 definitions, prompts (versioned files), output schemas
      tools/                 # agent tools (doc 09)
      services/
        literature/          # source adapters, dedup, snowballing, ranking, full text, chunking
        signals/             # GitHub, Hugging Face, Hacker News, RSS adapters
        datasets/            # dataset lookup
        mining/              # landscape map, limitation clusters, contradictions, matrix
        embeddings/          # embedder interface + implementations
        evidence/            # verifier + verdict engine + coverage report
        export/              # Markdown / PDF rendering
      db/                    # models, migrations
      observability/         # logging, ADK callbacks, cost accounting
      config/                # role models, budgets, rule tables, tiers
    tests/                   # unit, contract, integration, fixtures (doc 13)
    eval/                    # datasets, runners, baselines, reports (doc 13)
  frontend/                  # React app (doc 12)
  infra/                     # Dockerfiles, deployment scripts, DB setup (doc 14)
  mcp/                       # optional eureka-mcp server (doc 09), later
```

## 3. API

All routes are under `/api/v1`. They require authentication, except health checks.

### 3.1 Investigations (both modes)

| Method | Route | Purpose |
|---|---|---|
| POST | `/investigations` | Create. Body: mode, input (idea or field brief inputs), complexity, constraints (or "use my profile"). Returns the investigation ID |
| GET | `/investigations` | List the user's investigations |
| GET | `/investigations/{id}` | Status, stage, mode, summary, budgets and counters |
| GET | `/investigations/{id}/clarification` | The structured reading (facets or field brief) + questions |
| POST | `/investigations/{id}/clarification` | Answers / edits / skip → starts the main run |
| GET | `/investigations/{id}/events` | **SSE** stream of progress events |
| POST | `/investigations/{id}/cancel` | Cancel (stops at the next stage boundary) |
| GET | `/investigations/{id}/coverage` | Investigation-level coverage summary |
| GET | `/investigations/{id}/landscape` | Mode 2: subtopic map, limitation clusters, contradictions, combination hints |
| GET | `/investigations/{id}/signals` | Mode 2: tier-3 signals used |

### 3.2 Candidates

| Method | Route | Purpose |
|---|---|---|
| GET | `/investigations/{id}/candidates` | All candidates with status, verdict summaries, rejection reasons. Filters: status, origin |
| GET | `/candidates/{cid}` | Full candidate: facets, motivation, lineage, evaluations, verdicts, triage history |
| GET | `/candidates/{cid}/matrix` | Facet × paper matrix |
| GET | `/candidates/{cid}/claims` | Claims with evidence (filter by kind or status) |
| GET | `/candidates/{cid}/challenges` | Skeptic log |
| GET | `/candidates/{cid}/coverage` | Coverage report |
| GET | `/candidates/compare?ids=…` | Side-by-side comparison data |
| POST | `/candidates/{cid}/steer` | Mode 2 steering: more like this / simpler / exclude X / combine with Y → a Scout task |
| POST | `/candidates/{cid}/deep-dive` | Run Workflow A (full evaluation) on this candidate |
| POST | `/candidates/{cid}/proposal` | Generate the research package (async; progress over SSE) |
| GET | `/candidates/{cid}/proposal` | Get the research package |
| GET | `/candidates/{cid}/graph` | Evidence graph nodes and edges |

### 3.3 Papers, export, conversation, profile

| Method | Route | Purpose |
|---|---|---|
| GET | `/investigations/{id}/papers` | Paper library (sort/filter: relevance, year, tier, full text vs abstract, used by which candidate) |
| GET | `/papers/{pid}` | Metadata + analysis + quotes used |
| GET | `/investigations/{id}/report?format=md\|pdf` | Investigation report |
| GET | `/candidates/{cid}/proposal/export?format=md\|pdf\|docx` | Package export (docx later) |
| POST | `/investigations/{id}/messages` | "Ask about this investigation" (optional feature) |
| GET/PUT | `/profile` | Constraints profile |
| GET | `/health`, `/ready` | Health checks |

### 3.4 Progress event format

Each SSE event carries:

- event type: stage started / stage finished / counter update / candidate status change / warning / error / completed
- stage name
- a human-readable message
- counters
- cost so far
- the affected candidate ID, if any

Events are also stored, so a reconnecting client can replay them.

## 4. Background execution

- Creating or clarifying an investigation starts the orchestrator as a background task, and the request returns immediately.
- The orchestrator persists its state after each stage. On startup, the service looks for investigations in "running" status and resumes them.
- Concurrency limits: a maximum number of concurrent investigations per instance and per user, plus global semaphores for LLM calls and for each source.
- **Cloud Run note:** background work after the response needs CPU that stays allocated (instance-based billing or "CPU always allocated") and a minimum instance count of 1 during demos. Otherwise background tasks get throttled. Moving runs to Cloud Run Jobs/Tasks later removes this constraint.

## 5. Error handling summary

| Layer | Strategy |
|---|---|
| Source adapters | Retry with backoff → skip the source → record it in the coverage report |
| LLM calls | SDK retries → stage retry → partial stage (doc 08) |
| Verifier failures | Downgrade claims; never block the run |
| Orchestrator | Catch per stage; mark the stage failed/partial; continue where the rules allow; mark the investigation failed only if a mandatory stage (intake, any retrieval at all) fails |
| API | Consistent error responses with a correlation ID; no stack traces to clients |

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
