# 14: Deployment and Operations

## 0. As deployed (step E, 2026-11-24)

The live site differs from the plan below in two places: the frontend is on **Vercel** (free, made for Next.js) and the database is **Neon** (Postgres 17 + pgvector, free tier) instead of Cloud SQL. Also as deployed: Cloud Run runs the backend only, with min 0 / max 1 instances (runs execute
inside the API process, so one instance keeps them in one place until a job queue exists); a run's cost cap is 2.5× its
price (`EUREKA_RUN_CAP_FACTOR=2.5`, default 1.5, doc 18 §2); the sources are arXiv and OpenAlex (no Semantic Scholar or
GitHub keys); Vertex AI embeddings are not used by the runs (doc 06, as built).

| Part | Where | Settings |
|---|---|---|
| Website | Vercel, project `research-ai-agent`, root directory `frontend` | `BACKEND_URL` = the Cloud Run address; `/api/*` is forwarded there, so the login cookie stays first-party. Redeploys on every push to GitHub |
| API | Cloud Run service `eureka-api`, `europe-west3` (Frankfurt), built from the root Dockerfile (`gcloud run deploy --source .`; `.gcloudignore` keeps `.env` and the website out) | 1 CPU, 1 GiB; **max 1 instance** (runs, the login limiter and live streams live in memory), min 0 (sleeps when unused), CPU always allocated while awake (background runs), request timeout 3600 s (live streams). Migrations run on start |
| Settings | Secret Manager | `eureka-env` = the `.env` file, mounted at `/secrets/env/.env` (`EUREKA_ENV_FILE`); `eureka-database-url` = Neon's direct (not pooled) URL as `DATABASE_URL` (converted for asyncpg by `asyncpg_url`). Plain env vars override the file: `FRONTEND_ORIGIN`, `PUBLIC_SITE_URL` (the Vercel address), `EUREKA_COOKIE_SECURE=true` |
| Database | Neon, AWS Frankfurt | Copied from the local database with pg_dump / pg_restore (email tokens left out) |
| Permissions | The Compute Engine default service account | Secret Manager accessor, Vertex AI user, Cloud Run builder, log writer |

Redeploy the API: `gcloud run deploy eureka-api --source . --region europe-west3` (the other settings are kept). Cost control: max 1 instance that sleeps when idle; only accounts in `EUREKA_DEMO_ACCOUNTS` can buy Pro, and verification emails reach only the Resend account's own address, so strangers can't start free evaluations.

## 1. Environments

| Environment | Where | Database | LLM | Purpose |
|---|---|---|---|---|
| **Local** | Developer machines | PostgreSQL + pgvector in Docker | Anthropic API (dev key, low cost cap) | Development |
| **Test (CI)** | GitHub Actions | Ephemeral Postgres service container | **Recorded responses** (no live calls) | Unit, contract and integration tests |
| **Demo / production** | Cloud Run (backend) + Vercel (frontend) | Neon (PostgreSQL 17 + pgvector); the plan was Cloud SQL | Anthropic API (prod key) | Course demo and real use |

## 2. Google Cloud architecture

```text
User ──► Cloud Run: frontend (static build served by a small web server; or Firebase Hosting)
              │
              ▼
         Cloud Run: backend (FastAPI + orchestrator), min instances 1 during demos,
                    CPU always allocated (background runs)
              │
   ┌──────────┼──────────────┬────────────────────┬─────────────────┐
   ▼          ▼              ▼                    ▼                 ▼
Cloud SQL   Secret Manager  Vertex AI            Anthropic API     External APIs
(Postgres   (API keys)      text embeddings      (Claude)          (Semantic Scholar,
+pgvector)                  (optional)                             OpenAlex, arXiv, …)
              │
              ▼
         Cloud Logging / Monitoring (logs, metrics, alerts)
```

**Services and why:**

| Service | Use | Why |
|---|---|---|
| **Cloud Run** | Backend and frontend containers | Serverless, scales to zero outside demos; matches the course's deploy experience |
| **Cloud SQL for PostgreSQL** (as deployed: **Neon** instead) | Main database with pgvector | Managed Postgres; pgvector supported. Neon was chosen at deployment: a free tier, same Postgres 17 + pgvector, Frankfurt |
| **Secret Manager** | Anthropic, Semantic Scholar, GitHub keys | Secrets never live in code or images; mounted as environment variables |
| **Vertex AI** | Text embeddings (and optionally Claude through Vertex) | A meaningful Vertex AI use without Gemini |
| **Cloud Logging / Monitoring** | Structured logs, dashboards, alerts | Built in |
| **Artifact Registry + Cloud Build** | Container images | Standard |
| *(Later)* Cloud Tasks / Cloud Run Jobs | Long investigations outside the API process | Removes the need for always-allocated CPU |

## 3. Secrets

| Secret | Used by |
|---|---|
| Anthropic API key (workspace-scoped) | Backend (all LLM calls) |
| Semantic Scholar API key | Literature service |
| GitHub token | Signal service |
| Hugging Face token (optional) | Dataset lookup |
| Database password / connection | Backend |
| Auth configuration | Backend / frontend |

**Setup:**

1. Create each secret in Secret Manager.
2. Grant the Cloud Run service account the "Secret Manager Secret Accessor" role.
3. Mount the secrets as environment variables at deploy time.

Locally: a gitignored `.env`, with `.env.example` listing the names only. **Never commit keys. Never send keys to the frontend.**

## 4. Deployment process

1. Build the backend and frontend container images (Cloud Build).
2. Run database migrations as a one-off job before switching traffic.
3. Deploy to Cloud Run with the secrets mounted and the config version set.
4. Run the smoke test (one Mode 1 idea) against the new revision.
5. Shift traffic.

Rollback = route traffic to the previous revision.

**Note:** `adk deploy cloud_run` (from the course) deploys a bare ADK agent with ADK's own UI. Eureka is a FastAPI app with its own Dockerfile, so it uses a standard Cloud Run deploy. The ADK command is still handy for the week-1 spike.

## 5. Monitoring and alerts

| Signal | Alert when |
|---|---|
| Daily LLM spend (from call tables) | Above the daily cap (e.g. $X/day, set by you) |
| Investigation failure rate | >20% in an hour |
| Source error rate (per source) | Sustained errors (a source is down; the coverage report will show it) |
| Verifier failure rate | Sudden rise (prompt regression) |
| Hallucinated-ID count | Any non-zero value in production outputs |
| Latency per stage | Far above baseline |

Also set an **Anthropic Console spend limit** for the workspace as a hard backstop.

## 6. Cost controls (summary)

- A per-investigation cost cap and per-user daily limits (backend).
- An Anthropic workspace spend limit (Console).
- Caching (API cache, paper-analysis cache, prompt caching).
- Effort per role; an optional cheaper model for high-volume roles (doc 08).
- Cloud Run scales to zero outside demo periods; the database (Neon) is on its free tier.

## 7. Continuous integration

On every push:

1. Lint + type check.
2. Unit tests.
3. Contract tests against recorded LLM responses.
4. Integration tests on fixture corpora.
5. Build the containers.

Main-branch merges deploy automatically to the demo environment (optional; manual deploy is fine for the course).

## 8. Data and privacy

- What we store: user inputs, profiles, investigations, paper metadata and open-access text, and LLM call metadata.
- No sensitive personal data is needed. Profiles contain academic information only.
- Users can delete an investigation (cascades to its candidates, claims and messages; global paper records stay).
- Full text is stored for internal retrieval only. Exports contain quotes and references, not full texts.
