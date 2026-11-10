# Eureka: AI Research Discovery & Validation Agent

Eureka helps students and researchers decide **what to research**. It works in two modes:

| Mode | The user brings | Eureka delivers |
|---|---|---|
| **Evaluate My Research Idea** | An idea, question, hypothesis or topic | An evidence-backed assessment of novelty, significance and feasibility; the closest prior work; a skeptic's challenges; refinements or alternatives; a research proposal |
| **Find Me a Research Idea** | A broad field, interests, complexity level and constraints | Several research opportunities **discovered from the literature** (limitations, future work, contradictions, unexplored combinations), each one put through the same evaluation, with weak candidates rejected openly, plus research packages for the strongest ones |

Both modes run on the same infrastructure: one search layer, one evidence ledger, and one set of evaluation agents. The only difference is how the workflow starts. In Mode 2, Eureka **discovers first and evaluates afterwards**.

What makes Eureka more than "an LLM with a research theme":

- **Falsification first.** A separate Skeptic agent actively tries to find the paper that already did the idea.
- **Verified evidence.** Every important claim points to a paper and a passage. Quotes are checked by code, and paper references come only from our database, so citations can't be invented.
- **Honest uncertainty.** Verdicts are categories decided by written rules, and every verdict comes with a coverage report (what was searched, what wasn't). Eureka never claims an idea is guaranteed to be original.
- **Personalized feasibility.** Complexity level and user constraints shape both idea generation and evaluation.

> **Status:** design phase. These documents are the plan, written to read, discuss and refine before building. Eureka is a one-person project. No implementation code exists yet.

---

## How to read these documents

Read them in order the first time. Each document is self-contained enough to revisit on its own later.

| # | Document | What it answers |
|---|---|---|
| 01 | [Product vision](docs/01-product-vision.md) | Why Eureka exists, who it's for, how it differs from existing tools, core principles |
| 02 | [Operating modes and user inputs](docs/02-operating-modes-and-user-inputs.md) | The two modes, what the user enters, complexity levels, constraints, the research package Eureka delivers |
| 03 | [System architecture](docs/03-system-architecture.md) | The big picture: components, which parts are LLM agents and which are code, the framework choice, module boundaries |
| 04 | [Agents](docs/04-agents.md) | Every agent in detail: job, inputs, outputs, tools, model settings, failure modes. Includes the new **Research Opportunity Scout** |
| 05 | [Workflows and orchestration](docs/05-workflows-and-orchestration.md) | Workflow A and Workflow B step by step, the candidate lifecycle, iteration loops, budgets and stopping conditions |
| 06 | [Retrieval and RAG](docs/06-retrieval-and-rag.md) | Academic sources, discovery sources, search, dedup, ranking, full text, chunking, embeddings, caching, rate limits |
| 07 | [Evidence and verdicts](docs/07-evidence-and-verdicts.md) | The evidence ledger, source tiers, quote verification, verdict rules, coverage reports, how uncertainty is communicated |
| 08 | [LLM engineering](docs/08-llm-engineering.md) | How we call Claude: models, thinking/effort, structured outputs, prompts, context building, caching, cost, errors, conversation, ADK specifics |
| 09 | [Tools and MCP](docs/09-tools-and-mcp.md) | The tools agents can call, how they're designed, and where MCP fits (and where it doesn't) |
| 10 | [Data model](docs/10-data-model.md) | Entities, fields, relationships and statuses |
| 10+ | [ER model](docs/er-model.md) | The pipeline told through its data: six regions with ER diagrams, dependencies, invariants, an end-to-end trace, and an audit of the data model |
| 11 | [Backend and API](docs/11-backend-and-api.md) | Service layout, endpoints for both modes, progress streaming, jobs, configuration, security |
| 12 | [Frontend and UX](docs/12-frontend-and-ux.md) | Screens, the two entry points, candidate cards, comparison, evidence explorer, exports, MVP vs later |
| 13 | [Evaluation and testing](docs/13-evaluation-and-testing.md) | How we prove Eureka is better than baselines (both modes), metrics, test strategy |
| 14 | [Deployment and operations](docs/14-deployment-and-operations.md) | Google Cloud setup, secrets, logging, monitoring, cost controls, environments, CI |
| 15 | [Roadmap](docs/15-roadmap.md) | MVP vs optional features, the solo 4-week plan, cut lines, exit criteria |
| 16 | [Risks and open questions](docs/16-risks-and-open-questions.md) | What could go wrong, and the decisions we still need to make together |
| 17 | [Course references](docs/17-course-references.md) | Which course examples to learn from (read-only; we never change the course repo) |

---

## Glossary

| Term | Meaning in Eureka |
|---|---|
| **Investigation** | One run of Eureka for one user request, in either mode |
| **Candidate** | A research idea under evaluation. A user's own idea (Mode 1) and a Scout-generated idea (Mode 2) are both candidates, and both go through the same pipeline |
| **Facet** | One component of an idea: problem, method, data/setting, evaluation, contribution. Novelty is judged per facet |
| **Evidence ledger** | The database of claims, quotes and their links to papers |
| **Source level** | Where a piece of evidence came from: full text, abstract, metadata, or model inference |
| **Source tier** | How much a source can prove: peer-reviewed (tier 1), preprint (tier 2), or community/industry signal (tier 3, motivation only, never proof of novelty) |
| **Coverage report** | What was searched: sources, queries, date range, counts, failures, blind spots |
| **Saturation** | The point where new searches stop finding new relevant papers. A required stopping signal for the Skeptic |
| **Light / full evaluation** | Two depths of the evaluation pipeline. Light is used to screen many candidates cheaply; full is used for the finalists |
| **Research package** | The final deliverable for a strong candidate: 17 sections, from title to recommendation (see doc 02) |

---

## Principles (apply to every document)

1. Evidence over plausible-sounding answers.
2. Explicit uncertainty over fabricated confidence.
3. Code decides rules, LLMs do language and judgment. Anything that must be reliable (citations, verdict rules, budgets, orchestration) is deterministic code.
4. An agent exists only if independent reasoning adds measurable value.
5. Preserve and extend instead of duplicating. Both modes share one pipeline.
6. Human oversight: Eureka supports academic judgment and never certifies originality.
