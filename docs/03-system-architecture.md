# 03: System Architecture

## 1. Architectural principles

1. **One pipeline, two entry points.** Mode 1 and Mode 2 differ only in how candidates are created. Evaluation is shared.
2. **LLMs for language and judgment, code for everything that must be reliable.** Search, dedup, ranking, quote verification, verdict rules, budgets and orchestration are deterministic code.
3. **Generator and critic are separated.** The Scout proposes; the Novelty Investigator, Skeptic and Value & Feasibility Evaluator judge. No agent judges its own output.
4. **Agents communicate through typed records, not chat.** Agents write structured outputs to the database (the blackboard pattern). The orchestrator builds each agent's input as a curated *context packet*.
5. **Everything is replaceable behind an interface:** models, search sources, embedders, full-text fetchers.
6. **Budgets everywhere.** Every loop has a hard limit; every investigation has a cost and time cap.

## 2. Components

### 2.1 LLM components (7)

| ID | Component | Kind | Brief's role it covers |
|---|---|---|---|
| A1 | **Idea Analyst** | Single structured LLM call | Intake for both modes: idea facets (Mode 1), field brief (Mode 2), clarifying questions, query plans |
| A2 | **Research Opportunity Scout** (new) | **Agent with tools** | Discovers candidates (Mode 2), proposes alternatives when an idea fails (Mode 1), and refines rejected candidates (both modes). *Absorbs the former "Opportunity Generator"* |
| A3 | **Paper Reader** | Single structured call per paper, run in parallel | Research Analysis Agent: extracts question, methods, data, findings, limitations, future work, with quotes |
| A4 | **Novelty Investigator** | Structured LLM call (iterative) | Compares a candidate with the literature, builds the facet matrix, proposes claims |
| A5 | **Skeptic** | **Agent with tools** | Critical Reviewer: tries to refute novelty and significance claims with its own searches |
| A6 | **Value & Feasibility Evaluator** | Structured LLM call (two tasks: quick and full) | Research Value Evaluator + Feasibility Evaluator. Two separate verdicts from one component |
| A7 | **Research Proposal Designer** | Single structured call (streamed) | Turns a validated candidate + its evidence into the research package |

Only **A2 and A5 are true agents** (they choose their own tool calls in a loop). Everything else is a bounded structured call. That's deliberate: tool loops cost more and are harder to test, so we use them only where open-ended exploration is the job.

### 2.2 Code components (4)

| ID | Component | Responsibility | Brief's role it covers |
|---|---|---|---|
| C1 | **Orchestrator** | Runs the state machine for both workflows, manages the candidate lifecycle, applies triage rules, enforces budgets, persists state, emits progress events | Orchestrator |
| C2 | **Literature Discovery service** | Source adapters (Semantic Scholar, OpenAlex, arXiv, Crossref; PubMed later), dedup, citation snowballing, ranking, full-text fetching, chunking, embeddings, caching, rate limiting | Literature Discovery Agent |
| C3 | **Signal service** | Discovery-source adapters (GitHub, Hugging Face datasets, Hacker News, RSS news). Returns tier-3 *signals*, never evidence | New (supports the Scout) |
| C4 | **Evidence Verifier + Verdict Engine** | Checks IDs, quotes, tiers and source levels; computes verdicts and recommendations from rules | Part of "resolves conflicting conclusions" |

### 2.3 Why these merges and splits

| Decision | Reason | Alternative considered |
|---|---|---|
| Literature Discovery is a service, not an agent | Searching is I/O. LLM help is limited to writing queries, which A1, A2 and A5 do | An LLM search agent: more cost and nondeterminism, no quality gain |
| The Opportunity Generator is merged into the Scout | Discovering opportunities, proposing alternatives and refining candidates are all "generate grounded candidates from evidence" with the same output schema | Two generators: duplicated prompts and schemas, unclear ownership |
| Value and Feasibility share one component | They read the same context (candidate + evidence + constraints); one call gives a coherent judgment. The verdicts stay separate | Two agents: more calls, often contradictory framing |
| The Skeptic is its own agent | Independence is the point. It gets different instructions, its own search budget, and never sees the Novelty Investigator's reasoning text | Self-critique in the same call: well documented to agree with itself |
| The Paper Reader is idea-agnostic | Its analysis of a paper doesn't depend on the candidate, so it's **cached per paper and reused** across candidates and investigations (a big cost saving in Mode 2) | Idea-specific reading: re-reads the same paper for every candidate |
| The orchestrator is code | Reproducible, testable, resumable | An LLM supervisor: unpredictable routing, hard to test |

## 3. Architecture diagram

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Frontend (React)                                                             │
│  [Evaluate My Idea]  [Find Me an Idea]  progress · candidate board · evidence │
│  · comparison · proposal · export · "Ask about this investigation"            │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ REST + Server-Sent Events
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ FastAPI backend (Cloud Run)                                                  │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ C1 ORCHESTRATOR (code state machine, ADK custom agent)                       │
│                                                                              │
│  Workflow A (evaluate)            Workflow B (discover)                      │
│  A1 Idea Analyst                  A1 Idea Analyst (field brief)              │
│       │                           C2 landscape retrieval → A3 Paper Reader   │
│       │                           A2 Scout: mine + generate candidates       │
│       ▼                                 ▼                                    │
│  ┌────────────── Shared CANDIDATE EVALUATION pipeline ─────────────────┐     │
│  │ C2 targeted retrieval → A3 Paper Reader (cached) → A4 Novelty       │     │
│  │ → A5 Skeptic loop ⇄ A4 → A6 Value & Feasibility → C4 Verdicts       │     │
│  │ depth = light (screening) | full (finalists)                        │     │
│  └───────────────┬─────────────────────────────────────────────────────┘     │
│                  │ triage rules: advance / refine / reject / need more        │
│                  ├──► A2 Scout (refine or alternatives) ──► back into pipeline│
│                  ▼                                                           │
│        A7 Proposal Designer ──► C4 Verifier ──► research package / report     │
└───────┬──────────────────────────────┬───────────────────────────────────────┘
        │                              │
┌───────▼──────────────┐   ┌───────────▼──────────────────────────────────────┐
│ PostgreSQL + pgvector│   │ External services                                 │
│ investigations,      │   │ Anthropic API (Claude)                            │
│ candidates, papers,  │   │ Semantic Scholar · OpenAlex · arXiv · Crossref    │
│ analyses, chunks,    │   │ GitHub · Hugging Face · Hacker News · RSS (signals)│
│ claims, quotes,      │   │ Vertex AI embeddings (or local model)             │
│ verdicts, llm_calls, │   └───────────────────────────────────────────────────┘
│ api_cache            │
└──────────────────────┘
```

## 4. Framework choice: Google ADK with Claude

**Decision:** Google ADK (Agent Development Kit) for agent definitions, sessions and the orchestration skeleton, with ADK's `AnthropicLlm` model class so every agent runs on Claude through the Anthropic API.

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Google ADK + AnthropicLlm** | Matches the course syllabus (ADK, Cloud Run, Vertex). Custom agents allow deterministic orchestration. Has loop/sequential/parallel building blocks, sessions and callbacks for logging/cost | Claude integration has quirks (doc 08) | **Chosen** |
| LangGraph | Excellent typed state, checkpointing, human-in-the-loop interrupts | Second framework to learn; less tied to the Google Cloud part of the syllabus | Strong alternative. **Don't mix the two** |
| Plain Anthropic SDK | Full control, no framework magic | Loses the "orchestration framework" course points; more glue code | Used *inside* services where ADK doesn't expose a feature (Citations, Batches, token counting) |
| Claude Agent SDK / Managed Agents | Batteries-included agent harness | Built for coding/filesystem agents; less control over our evidence pipeline | Not a fit |

**How ADK is used:**

- The orchestrator is an ADK **custom agent** whose run logic implements the state machines in doc 05.
- A1, A3, A4, A6 and A7 are ADK LLM agents with an **output schema** (Pydantic models described in doc 10) and an output key.
- A2 (Scout) and A5 (Skeptic) are ADK LLM agents with **function tools** (doc 09).
- The Skeptic ⇄ Novelty revision uses ADK's **loop agent** with a maximum iteration count and an early exit on saturation.
- Dynamic fan-out (per-paper reading, per-candidate light evaluation) uses plain async concurrency with semaphores inside the orchestrator. ADK's parallel agent needs a fixed set of sub-agents.
- **Model callbacks** (before/after each LLM call) record tokens, cost, latency and role, and enforce budgets.
- ADK **session state** holds only IDs and stage markers. Domain data (papers, claims, candidates) lives in our own tables.

## 5. Module boundaries (interfaces)

Each boundary is a small interface, so implementations can be swapped without touching the agents.

| Interface | Responsibility | Implementations (MVP → later) |
|---|---|---|
| Search source | Search by query + filters; optional references, citations and recommendations | Semantic Scholar, OpenAlex, arXiv → Crossref (verification), PubMed, CORE |
| Signal source | Return tier-3 signals for a topic | GitHub, Hugging Face datasets → Hacker News, RSS news, Reddit |
| Dataset lookup | Check whether named datasets exist and are accessible | Hugging Face datasets → Kaggle, OpenML, Zenodo |
| Full-text fetcher | Get and parse open-access full text | arXiv PDFs, Semantic Scholar/OpenAlex open-access links → GROBID |
| Embedder | Turn text into vectors | Local SPECTER2 / bge model, Vertex AI text embeddings |
| LLM model per role | Map each role to a model + effort | Configuration (doc 08) |
| Verdict rules | Map structured assessments to verdict categories | Versioned rule tables (doc 07) |

**Adding a new source** means writing one adapter, registering it in config, and giving it a tier. No agent or workflow changes.

## 6. What changed from the previous architecture (v1 → v2)

| Area | v1 (single-mode) | v2 (two modes) | Why |
|---|---|---|---|
| Entry | One workflow | Two workflows sharing one evaluation pipeline | Mode 2 requirement without duplication |
| Core abstraction | "The idea" | **Candidate** (with versions and lineage) | Lets user ideas, Scout ideas, alternatives and refinements flow through one pipeline |
| Opportunity Generator (A6 in v1) | Separate agent | **Merged into the Scout** | Same job, same schema |
| Paper analysis | Per investigation | **Per paper, cached globally, idea-agnostic** | Mode 2 reads many papers across many candidates |
| Evaluation | One depth | **Light and full** depths | Screening 6+ candidates at full depth would blow the budget |
| Feasibility | One call at the end | **Quick check** (pre-screen) + **full** check | Reject infeasible candidates before spending on them |
| Sources | Academic only | Academic + **tier-3 signal sources** | Mode 2 discovery requirement, with reliability guardrails |
| Loops | Skeptic ⇄ Novelty | + **Triage loop**: refine / reject / request more candidates | The iterative-workflow requirement |
| Inputs | Profile | **Complexity level** + structured constraints, hard vs soft | The complexity requirement |
