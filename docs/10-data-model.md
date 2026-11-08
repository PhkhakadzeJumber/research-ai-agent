# 10: Data Model

The data model has two jobs. It is the **database schema** (PostgreSQL + pgvector), and it defines the **structured shapes** that agents output (implemented later as Pydantic models used as ADK output schemas).

This document describes entities in plain tables. It deliberately contains no code.

## 1. Entity overview

```text
User ─┬─ ConstraintsProfile
      └─ Investigation (mode: evaluate | discover)
             ├─ FieldBrief (Mode 2) / StructuredIdea (Mode 1)
             ├─ SearchQuery ──► InvestigationPaper ──► Paper ─┬─ Chunk
             │                                                ├─ PaperAnalysis (global, cached)
             │                                                └─ Quote
             ├─ Signal (tier 3)
             ├─ OpportunityMaterial (clusters, contradiction pairs, matrix)
             ├─ Candidate (versions, lineage)
             │     ├─ Facet
             │     ├─ Evaluation (light | full)
             │     │     ├─ FacetMatch
             │     │     ├─ Claim ──► ClaimEvidence ──► Quote
             │     │     ├─ Challenge (Skeptic)
             │     │     ├─ Verdict (novelty | significance | feasibility)
             │     │     └─ CoverageReport
             │     ├─ TriageDecision
             │     └─ ResearchPackage
             ├─ ConversationMessage
             └─ ProgressEvent
LlmCall, ToolCall, ApiCache (operational)
```

## 2. Core entities

### User and ConstraintsProfile

| Field | Description |
|---|---|
| user id, email, created at | Basic identity (simple auth) |
| academic level | undergraduate / master / phd / researcher |
| time available | Total weeks + hours per week |
| skills | List + free text |
| background | Free text |
| compute tier | T0–T3 + free text |
| data access | public only / can collect / institutional |
| budget | none / small / some |
| preferred technologies, subfields | Lists |
| ethics constraints | List |
| supervision | yes / limited / no |
| other | Free text |

### Investigation

| Field | Description |
|---|---|
| id, user id | — |
| mode | **evaluate** or **discover** |
| input | The raw user input (idea text, or field + interests + exclusions + domain) |
| complexity level | beginner / intermediate / advanced |
| constraints snapshot | A copy of the profile at run time (so later profile edits don't change past results) |
| status | created / clarifying / running / awaiting user / completed / failed / cancelled |
| stage | The current state-machine stage (doc 05) |
| budgets | Limits and counters (papers screened/read, rounds, cost, time) |
| cost so far, timings | — |
| assumptions | Recorded when clarification was skipped |
| config version | Versions of the prompts, rule tables and models used (reproducibility) |

### StructuredIdea (Mode 1) / FieldBrief (Mode 2)

| StructuredIdea field | FieldBrief field |
|---|---|
| title, research question | field, subtopics (4–8) with why each fits |
| facets | landscape query plan |
| assumptions, vagueness flags | signal queries |
| clarifying questions + answers | clarifying questions + answers |

### Paper (global)

| Field | Description |
|---|---|
| internal id | Stable ID used everywhere by agents |
| doi, arxiv id, semantic scholar id, openalex id | External IDs (any may be missing) |
| title, abstract, year, venue, authors | Metadata from the sources |
| citation count, open-access PDF URL | — |
| tier | 1 (peer-reviewed) / 2 (preprint) |
| embedding + embedding model id | Title + abstract vector |
| full text status | none / fetched / parsed / failed |

### Chunk

Paper id, section title, text, position, embedding (+ model id), keyword-search index.

### PaperAnalysis (global, cached per paper + extractor version)

| Field | Description |
|---|---|
| paper id, extractor version, model, source level | — |
| research questions, tasks, setting | — |
| methods, datasets, metrics, baselines | Names exactly as written |
| findings | Each: question/setting, claim, direction, quote id |
| limitations, future work, assumptions | Each with a quote id |
| contribution type | — |

### Quote

Paper id, chunk id (empty = abstract), exact text, source level, **verified** flag, verification score.

### InvestigationPaper

Links a paper to an investigation:

- which queries found it,
- relevance scores (embedding and LLM),
- flags: screened / read / full text,
- which candidates it was used for.

### SearchQuery

Investigation, origin (analyst / scout / skeptic / landscape), source, query text, filters, result count, error.

### Signal (tier 3)

Investigation, source (github / huggingface / hackernews / news), URL, title, summary, date, popularity metric, topic tags.

### OpportunityMaterial (Mode 2)

| Type | Content |
|---|---|
| Subtopic cluster | Label, member paper IDs, representative papers, per-year counts |
| Limitation cluster | Label, member quote IDs, number of distinct papers, number of distinct author groups |
| Contradiction pair | Two finding quote IDs, shared question/setting, check result |
| Combination hint | Method, task, row/column counts, neighbouring examples |
| Dataset hint | Dataset name, tasks, availability result |

### Candidate

| Field | Description |
|---|---|
| id, investigation id | — |
| version, parent candidate id | Lineage of refinements |
| origin | user / scout discover / scout alternative / scout refine / scout replenish |
| origin strategy | limitation / future work / contradiction / combination / practical problem / refinement |
| title, research question, hypothesis | — |
| motivation | Text + inspiration links (quote IDs; signal IDs listed separately) |
| potential contribution (initial) | The Scout's claim, to be judged |
| complexity fit, constraint notes | The Scout's hypotheses |
| change note | For refinements: what changed and why |
| status | proposed / screened out / light evaluating / needs refinement / advanced / full evaluating / finalist / rejected / selected / proposal ready |
| rejection reason | Triage rule + evidence |
| signal-only flag | — |

### Facet

Candidate, kind (problem / method / data-setting / evaluation / contribution), text, is-key flag.

### Evaluation

Candidate, depth (light / full), started/finished, counters, status.

### FacetMatch

Evaluation, facet, paper, overlap (same / similar / different / not addressed / unknown), quote id, rationale.

### Claim and ClaimEvidence

- **Claim:** evaluation, kind (prior work / gap / significance / feasibility / limitation / contradiction), text, statement type (evidence / interpretation / hypothesis), inferred flag, status (verified / unverified quote / no evidence / challenged / refuted), author agent.
- **ClaimEvidence:** claim, quote, stance (supports / contradicts / qualifies).

### Challenge

Evaluation, round, target claim, type, argument, evidence quote IDs, new paper IDs, resolution (accepted / rebutted / unresolved) with a rebuttal claim link.

### Verdict

Evaluation, dimension (novelty / significance / feasibility / recommendation), category, rule trace (rule IDs + claim IDs), uncertainties, blocking requirements (feasibility), rule-table version.

### CoverageReport

Evaluation, sources and failures, query counts by origin, date range, the paper funnel counts, full-text vs abstract counts, tier mix, Skeptic saturation curve, budgets hit, blind spots.

### TriageDecision

Candidate, evaluation, rule number, action (advance / refine / reject / expand search / finalize), evidence IDs, timestamp.

### ResearchPackage

Candidate, the 17 sections (each with content + statement-type tags + referenced claim/paper IDs), generation model and prompt version, export history.

### ConversationMessage

Investigation, role (user / eureka), text, referenced IDs, linked actions (e.g. "steer Scout: simpler").

### ProgressEvent

Investigation, time, stage, message, counters snapshot, cost so far.

## 3. Operational entities

| Entity | Purpose |
|---|---|
| **LlmCall** | Investigation, agent role, model, effort, prompt version, input/output/cache tokens, cost, latency, API request ID, stop reason, error |
| **ToolCall** | Investigation, agent, tool, arguments, result size, latency, budget remaining, error |
| **ApiCache** | Source, normalized request key, response, fetched at, TTL |

## 4. Important constraints and indexes

- Paper external IDs are unique where present (used for dedup).
- PaperAnalysis is unique per (paper, extractor version).
- Vector indexes on paper and chunk embeddings; a keyword index on chunks (hybrid search).
- Candidates keep their full lineage; nothing is deleted on refinement.
- The investigation's constraints snapshot and config version make every result reproducible and explainable later.

## 5. Agent output shapes (summary)

These are implemented later as schemas, one per agent output:

| Shape | Producer |
|---|---|
| StructuredIdea, FieldBrief, QueryPlan | A1 |
| CandidateSet | A2 |
| PaperAnalysis | A3 |
| NoveltyAssessment (facet matrix, closest works, claims, proposed category, uncertainties, challenge responses) | A4 |
| ChallengeSet (challenges + saturation statistic) | A5 |
| QuickFeasibility; SignificanceAssessment + FeasibilityAssessment | A6 |
| ResearchPackage | A7 |

All of them reference papers by internal ID only and carry quotes as text + paper ID + chunk ID, ready for the verifier.
