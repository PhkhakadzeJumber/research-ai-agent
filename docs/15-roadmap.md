# 15: Roadmap

## 1. Scope

### 1.1 Essential MVP (must ship)

**Shared infrastructure**

- Academic retrieval (Semantic Scholar, OpenAlex, arXiv; Crossref for verification) with caching, dedup, snowballing and ranking.
- Embeddings + pgvector.
- Full text for the top open-access papers.

**Agents**

- A1 Idea Analyst
- A2 Scout (Discover, Alternatives, Refine)
- A3 Paper Reader (cached)
- A4 Novelty Investigator
- A5 Skeptic
- A6 Value & Feasibility
- A7 Proposal Designer (basic)

**Reliability**

- Evidence ledger, verifier, verdict engine, coverage reports.
- Triage and the candidate lifecycle, with budgets.

**Workflows**

- Workflow A: full evaluation, refine/alternatives loop.
- Workflow B: landscape retrieval, opportunity mining, Scout, pre-screen, light evaluation, triage, full evaluation of finalists.

**Inputs:** complexity levels + constraints profile (hard and soft constraints).

**Signals:** Hugging Face datasets (also dataset lookup) + GitHub (small scope).

**API + UI**

- Two entry points; forms; clarification; progress.
- Candidate board with rejection reasons.
- Workspace tabs: Overview, Prior work, Papers, Evidence, Skeptic log, Feasibility.
- Proposal view + Markdown export.

**Deploy:** Cloud Run + Cloud SQL + Secret Manager.

**Evaluation:** baselines B1/B2 + Eureka on the dataset in doc 13 (time-travel test excluded).

### 1.2 High-value extensions (week 4, in this order)

1. Candidate comparison view.
2. Mode 2 steering buttons (more like this / simpler / exclude).
3. Evidence graph.
4. PDF export.
5. Ablation runs + an evaluation dashboard page.
6. Hacker News / RSS signals.

### 1.3 Later / future

- "Ask about this investigation" chat panel.
- eureka-mcp server.
- Landscape view.
- PubMed and other domains; GROBID parsing; Citations-API evidence mode.
- Time-travel discovery evaluation; Message Batches for evaluation runs.
- Collaboration, project tracking, dataset evaluation, experiment automation, reproducibility analysis.

## 2. Four-week plan

**Team split** (adjust to your team size):

- **R:** retrieval and data
- **A:** agents and orchestration
- **F:** frontend
- **E:** evaluation and infrastructure

### Week 1: Foundations + shared infrastructure + baselines

| Who | Tasks |
|---|---|
| R | Repo skeleton; Postgres + pgvector in Docker; migrations for the core entities (doc 10); Semantic Scholar/OpenAlex/arXiv adapters with cache, rate limiter, retry and recorded fixtures; dedup; embedder interface (local model) |
| A | **ADK + Claude spike:** one structured-output agent and one tool-using agent running on Claude through the Anthropic model class. Confirm the adaptive-thinking requirement for structured outputs (doc 08), callbacks for cost logging, and a workspace-scoped key |
| A | Data shapes for all agent outputs; shared prompt preamble; prompt file versioning |
| E | Choose the demo domain; request the Semantic Scholar API key (day 1, approval can take time); build the first 10 Mode 1 evaluation cases; implement baselines B1/B2 |
| F | UI skeleton: home with two entry points, both forms, complexity selector, constraints panel (static) |

**Exit criterion:** a command-line run turns an idea into a ranked, deduplicated paper list, with costs logged. The baselines run on 10 cases. The ADK spike is confirmed.

### Week 2: Workflow A end to end (the reasoning core)

| Who | Tasks |
|---|---|
| R | Full-text fetch + chunking; hybrid passage retrieval; snowballing; relevance screening |
| A | A1, A3 (with the global cache), A4, A5 (tools + loop + saturation), A6; the candidate abstraction; triage rules; Workflow A state machine with persistence and budgets |
| A/E | Evidence verifier + verdict engine + coverage report; unit tests for rules |
| F | Progress view against a mocked event stream; workspace tabs against mocked data |
| E | Finish the Mode 1 evaluation set (~25 cases); first evaluation run |

**Exit criterion:** a command-line run of Workflow A produces a full report with verdicts, evidence, coverage, Skeptic log and alternatives. Tests are green. First evaluation numbers exist.

### Week 3: Workflow B + product + cloud

| Who | Tasks |
|---|---|
| R | Landscape retrieval; opportunity miners (limitation clusters, contradiction pairs, method × problem matrix, landscape map); Hugging Face + GitHub signal adapters; dataset lookup |
| A | A2 Scout (Discover/Alternatives/Refine/Replenish); pre-screen; light vs full evaluation; Workflow B state machine; A7 basic package |
| F | Connect the UI to the real API + server-sent events; candidate board; rejection reasons; proposal view |
| E | Deploy to Cloud Run + Cloud SQL + Secret Manager; Vertex embeddings; Mode 2 evaluation cases (field × profile, planted candidates); human rubric guide |

**Exit criterion:** both modes run end to end from the browser on the deployed instance.

### Week 4: Extensions, evaluation, polish

| Who | Tasks |
|---|---|
| All | Extensions in priority order (section 1.2) as time allows |
| E | Full evaluation: B1, B2, Eureka, ablations, 3× consistency; human rubric scoring; results write-up |
| A | Prompt/rule fixes driven by evaluation failures (with re-runs); failure-injection tests |
| F | UX polish, exports, empty/error states |

**Feature freeze on day 24.** The last days are for the demo script, the report, a pre-warmed cache and buffer.

## 3. Definition of done (per feature)

- Implemented behind the agreed interface.
- Unit/contract tests written and passing.
- Costs and errors logged.
- Shown correctly in the UI with honest labels.
- Documented: the relevant doc in `docs/` is updated if the design changed.

## 4. Order of risk retirement

Do the riskiest things first:

1. The ADK + Claude integration (week 1 spike).
2. Retrieval quality and rate limits (week 1).
3. Verdict rules + verifier (week 2).
4. Scout grounding quality (week 3, early).
5. Cloud deployment of background runs (week 3).
