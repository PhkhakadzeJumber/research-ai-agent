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

## 2. Four-week plan (solo)

Eureka is built by **one person**. So the plan is a sequence of focused blocks rather than parallel workstreams, and every week ends with something that runs. Claude Code is part of the toolset: it handles boilerplate (adapters, migrations, tests, UI components), so the solo time goes into design, prompts, rules and evaluation.

**Honest assessment:** the full MVP in section 1.1 is ambitious for one person in 4 weeks. The plan therefore has **cut lines** (section 2.5). Check them at the end of each week and apply them without hesitation; a smaller system that works and is evaluated honestly beats a bigger one that doesn't.

### 2.1 Week 1: Foundations + baselines

| Days | Tasks |
|---|---|
| 1 | **Accounts and keys:** workspace-scoped Anthropic key; request the Semantic Scholar API key (approval can take time); GCP project with billing. **ADK + Claude spike:** one structured-output agent + one tool-using agent on Claude; confirm the adaptive-thinking requirement for structured outputs (doc 08) and callback-based cost logging |
| 2–3 | Repo skeleton; Postgres + pgvector in Docker; migrations for the core entities (doc 10); Semantic Scholar / OpenAlex / arXiv adapters with cache, rate limiter, retry and recorded fixtures; dedup (including preprint/published merging) |
| 4 | Embedder interface (local model); ranking; data shapes for all agent outputs; shared prompt preamble; prompt file versioning |
| 5 | Choose the demo domain; write the first 10 Mode 1 evaluation cases; implement baselines B1/B2 |

**Exit criterion:** a command-line run turns an idea into a ranked, deduplicated paper list, with costs logged. The baselines run on 10 cases. The spike is confirmed.

### 2.2 Week 2: Workflow A end to end (the reasoning core)

| Days | Tasks |
|---|---|
| 1 | Full-text fetch + chunking; hybrid passage retrieval; relevance screening |
| 2 | A1 Idea Analyst; A3 Paper Reader with the global cache; quote verifier |
| 3 | A4 Novelty Investigator; verdict engine + coverage snapshot; table-driven unit tests for the rules |
| 4 | A5 Skeptic (tools + loop + measured saturation); A6 Value & Feasibility; triage rules |
| 5 | Workflow A state machine with persistence and budgets; command-line report; first evaluation run on 10 cases |

**Exit criterion:** a command-line run of Workflow A produces a full report with verdicts, evidence, coverage and Skeptic log. Tests are green. First evaluation numbers exist.

### 2.3 Week 3: Workflow B + API

| Days | Tasks |
|---|---|
| 1 | Landscape retrieval; opportunity miners (limitation clusters, contradictions, method × problem matrix); Hugging Face dataset lookup + signals |
| 2–3 | A2 Scout (Discover / Alternatives / Refine); CandidateInspiration grounding check; pre-screen; light vs full evaluation; Workflow B state machine |
| 4 | FastAPI endpoints + server-sent events for both modes; A7 Proposal Designer (basic package) |
| 5 | Mode 2 evaluation cases (field × profile, planted candidates); complete the Mode 1 set to ~25 cases |

**Exit criterion:** both modes run end to end through the API.

### 2.4 Week 4: UI, deployment, evaluation, polish

| Days | Tasks |
|---|---|
| 1–2 | UI: two entry points, forms, clarification, progress, candidate board with rejection reasons, workspace tabs, proposal view + Markdown export |
| 3 | Deploy: Cloud Run + Cloud SQL + Secret Manager; smoke tests; pre-warm the demo cache |
| 4 | **Feature freeze.** Full evaluation: B1, B2, Eureka (+ the no-Skeptic ablation if time allows); human rubric scoring |
| 5 | Results write-up, demo script, buffer |

### 2.5 Cut lines (apply in this order if behind)

| If behind at… | Cut | What remains true |
|---|---|---|
| End of week 1 | GitHub signals; full-text parsing beyond arXiv | Retrieval works on abstracts + arXiv full text |
| End of week 2 | Snowballing; Skeptic limited to 2 rounds | The Skeptic still runs and saturation is still measured |
| End of week 3 | Mode 2 Refine/Replenish loops (keep reject + advance); full evaluation of finalists → light only, labelled as such | Mode 2 still discovers, grounds, screens and evaluates; verdicts stay honest (light can't say "Supported") |
| Start of week 4 | React UI → **Streamlit** UI; Vertex embeddings → local embeddings in the container | Both modes are usable in a browser and deployed |
| Day 4 of week 4 | Ablations; time-travel test | The main comparison (B1, B2, Eureka) is done |

**Never cut:** the quote verifier, code-computed verdicts, the coverage snapshot, and the "rejections are shown with reasons" rule. They are what makes Eureka different.

## 3. Definition of done (per feature)

- Implemented behind the agreed interface.
- Unit/contract tests written and passing.
- Costs and errors logged.
- Shown correctly in the UI with honest labels.
- Documented: the relevant doc in `docs/` is updated if the design changed.

## 4. Order of risk retirement

Do the riskiest things first:

1. The ADK + Claude integration (day 1 spike).
2. Retrieval quality and rate limits (week 1).
3. Verdict rules + verifier (week 2).
4. Scout grounding quality (week 3, early).
5. Cloud deployment of background runs (week 4, day 3: deploy a skeleton earlier if time allows).
