# 14: Deployment and Operations

## 1. Environments

| Environment | Where | Database | LLM | Purpose |
|---|---|---|---|---|
| **Local** | Developer machines | PostgreSQL + pgvector in Docker | Anthropic API (dev key, low cost cap) | Development |
| **Test (CI)** | GitHub Actions | Ephemeral Postgres service container | **Recorded responses** (no live calls) | Unit, contract and integration tests |
| **Demo / production** | Google Cloud | Cloud SQL (PostgreSQL + pgvector) | Anthropic API (prod key) or Claude on Vertex AI | Course demo and real use |

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
| **Cloud SQL for PostgreSQL** | Main database with pgvector | Managed Postgres; pgvector supported |
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
| Daily LLM spend (from call tables) | Above the daily cap (e.g. $X/day, set by the team) |
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
- Cloud Run scales to zero outside demo periods; Cloud SQL uses the smallest tier that works.

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
