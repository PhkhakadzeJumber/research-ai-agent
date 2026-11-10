# 10: Data Model

The data model has two jobs. It is the **database schema** (PostgreSQL + pgvector), and it defines the **structured shapes** that agents output (implemented later as Pydantic models used as ADK output schemas).

This document describes entities in plain tables. It deliberately contains no code.

**Why read this before the LLM engineering doc:** every LLM agent in Eureka has one job, which is to fill in a specific data shape. Once you know what each entity means and why each field exists, each agent's prompt becomes an explanation of how to fill that shape well. Section 9 connects each agent's output to the tables it ends up in.

> **Version 2.** This version applies the audit in [er-model.md](er-model.md) (section 9):
> - **Merged:** ConstraintsProfile into User; StructuredIdea and FieldBrief into Intake; CoverageReport into Evaluation.
> - **Deferred:** ConversationMessage and ToolCall.
> - **Corrected:** InvestigationPaper became RetrievalHit, scoped per evaluation; SearchQuery gained an evaluation scope; candidate lineage now has separate refinement and alternative links; the PaperAnalysis key includes the source level.
> - **Added:** CandidateInspiration.
>
> The ER model shows how these entities depend on each other; this document explains every field.

---

## 0. How to read this document

### 0.1 Four kinds of data

| Kind | What it is | Lifetime | Entities |
|---|---|---|---|
| **User data** | Who the user is and what they can do | Long-lived, editable | User (with profile) |
| **Knowledge data (global)** | Facts about the scientific world, shared by all investigations | Long-lived, reused, grows over time | Paper, Chunk, PaperAnalysis, Quote |
| **Investigation data** | Everything produced by one run of Eureka | Belongs to one investigation; deleted with it | Investigation, Intake, SearchQuery, RetrievalHit, Signal, OpportunityItem, Candidate, Facet, CandidateInspiration, Evaluation, FacetMatch, Claim, ClaimEvidence, Challenge, Verdict, TriageDecision, ResearchPackage, ProgressEvent |
| **Operational data** | How the system behaved (cost, cache) | Kept for monitoring and evaluation | LlmCall, ApiCache |

The split between **global knowledge** and **investigation data** is the most important design choice in this document:

- A paper and its analysis are the same whoever asks about them, so they are stored once and reused. This is what makes Mode 2 affordable.
- A *judgment* about a paper (e.g. "this paper does the same thing as candidate 3", or "this paper is relevant to candidate 3") depends on the candidate, so it belongs to the investigation, and more precisely to one evaluation.

A useful test when adding any new field: *"Would this value be the same for every user and every idea?"* If yes, it belongs in the global library. If no, it's investigation data.

### 0.2 Three kinds of fields

| Kind | Who fills it | Example |
|---|---|---|
| **Input fields** | The user (through the UI) | Idea text, complexity level |
| **LLM fields** | An agent, through its output schema | A claim's text, a facet match label |
| **System fields** | Code: the orchestrator, verifier, verdict engine, retrieval | IDs, timestamps, `verified` flags, verdict categories, counters |

A rule that runs through the whole model: **an LLM never fills a field that decides trust.**

- Verification status, verdict categories, tiers and source levels are system fields.
- The LLM proposes; code checks and decides.

### 0.3 Running example

Many field explanations use one running example:

- **User:** an undergraduate with 3 months, 10 hours per week, Python and basic PyTorch, a free cloud notebook GPU (compute tier T1).
- **Mode 1 idea:** *"Does retrieval-augmented generation reduce hallucinations of small language models in question answering for low-resource languages?"*
- **Mode 2 request:** field *NLP*, interests *"LLM evaluation, low-resource languages"*, complexity *Beginner*.

---

## 1. Entity overview

```text
User (with profile)
  └─ Investigation (mode: evaluate | discover)
        ├─ Intake (A1's reading + user edits; Mode 1 idea or Mode 2 field brief)
        ├─ SearchQuery (scope: landscape or one evaluation) ──► RetrievalHit ──► Paper (global)
        │                                                                  ├─ Chunk
        │                                                                  ├─ PaperAnalysis
        │                                                                  └─ Quote
        ├─ Signal (tier 3, Mode 2)
        ├─ OpportunityItem (mined patterns, Mode 2) ──► Quotes
        ├─ Candidate (versions: parent / spawned-from / root)
        │     ├─ Facet
        │     ├─ CandidateInspiration ──► Quote | OpportunityItem | Signal
        │     ├─ Evaluation (light | full, with frozen coverage snapshot)
        │     │     ├─ FacetMatch ──► Facet, Paper, Quote
        │     │     ├─ Claim ──► ClaimEvidence ──► Quote
        │     │     ├─ Challenge (Skeptic) ──► target Claim, rebuttal Claim
        │     │     └─ Verdict (novelty | significance | feasibility | recommendation)
        │     ├─ TriageDecision ──► (new Candidate when refining)
        │     └─ ResearchPackage ──► the Evaluation it is grounded in
        ├─ ProgressEvent
        └─ LlmCall
ApiCache (operational, global)
Deferred: ConversationMessage, ToolCall (section 8)
```

### 1.1 Who writes and who reads each entity

| Entity | Written by | Read by |
|---|---|---|
| User (with profile) | User (UI) | Investigation (snapshot), Scout, Value & Feasibility, Proposal |
| Investigation | API (create), orchestrator (status/stage/budget) | Everything |
| Intake | A1 Idea Analyst + user edits at clarification | Orchestrator (query plans, Candidate v1), Scout |
| SearchQuery | Orchestrator, Scout tools, Skeptic tools | Coverage snapshot, UI |
| RetrievalHit | Retrieval + ranking | Paper selection for reading, paper library UI, coverage |
| Paper, Chunk | Literature Discovery service | All agents (through context packets and tools) |
| PaperAnalysis | A3 Paper Reader | Novelty, Skeptic, mining code, Proposal |
| Quote | Proposed by A2–A7; **verified by C4** | Claims, facet matches, inspirations, opportunity items, UI evidence chips |
| Signal | Signal service | Scout only |
| OpportunityItem | Mining code | Scout |
| Candidate | A2 Scout or the user (Mode 1); status set by the orchestrator | Everything downstream |
| Facet | A1 (normalization) | Novelty, Skeptic, retrieval scoring |
| CandidateInspiration | A2 Scout; grounding checked by code | Pre-screen, UI ("inspired by") |
| Evaluation | Orchestrator | UI, triage, verdict engine |
| FacetMatch, Claim | A4 Novelty (+ A6 and A5 for some claims) | Verdict engine, UI |
| ClaimEvidence | Agents propose; verifier confirms | Verdict engine, UI |
| Challenge | A5 Skeptic; resolution by A4 + rules | Verdict engine, UI Skeptic log |
| Verdict | **C4 Verdict engine (code)** | Triage, UI, Proposal |
| TriageDecision | Orchestrator (rules) | UI ("why"), lineage, loop limits |
| ResearchPackage | A7 Proposal Designer; references rendered by the backend | UI, export |
| ProgressEvent | Orchestrator | UI progress view |
| LlmCall | ADK model callbacks | Cost/budget control, monitoring, evaluation |
| ApiCache | Source adapters | Source adapters |

---

## 2. User data

### 2.1 User (with profile)

**What it is:** a person using Eureka, together with their **constraints profile**: what they know, how much time and compute they have, and what they must avoid.

**Why it exists:**

- To own investigations, and to enforce per-user rate limits and cost caps.
- Feasibility is meaningless without the profile ("feasible for whom?"), and the profile also steers the Scout away from ideas the user can't execute.
- Stored once and reused, so users don't re-enter it on every run.

*(Earlier versions had a separate ConstraintsProfile entity. There's exactly one profile per user, and every investigation takes a snapshot of it anyway, so it's a field group on User.)*

**Hard vs soft:**

- Hard profile fields (compute, time, data access, ethics) are enforced by code filters.
- Soft fields (skills, preferences) are weighed by the LLM.

**Identity fields**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| user id | Internal unique identifier | Every user-owned row points to it | `u_17` |
| email | Login identity | Authentication | (from the auth provider) |
| created at | When the account was created | Basic auditing | 2026-11-10 |

**Profile fields**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| academic level | undergraduate / master / phd / researcher | Sets the expected depth and the default ambition; the Proposal Designer adapts its language and scope | undergraduate |
| time available | Total weeks + hours per week | Feasibility estimates a timeline per phase and compares it with this. **Hard** | 12 weeks, 10 h/week |
| skills | List + free text of what the user can already do | Feasibility flags skill gaps; the Scout prefers methods the user knows | Python, basic PyTorch, pandas |
| background | Courses taken, prior projects | Helps judge how fast missing skills can be learned | "Intro ML course, one NLP class project" |
| compute tier | T0 laptop / T1 free notebook GPU / T2 one GPU for days / T3 cluster, + free text | The most common reason an idea is infeasible. **Hard** | T1, "free notebook GPU" |
| data access | public only / can collect / institutional | Whether a candidate that needs new data is possible. **Hard** | public only |
| budget | none / small / some | Paid APIs, annotation, cloud costs | none |
| preferred technologies, subfields | Lists | Steer the Scout and query planning (soft) | Hugging Face, small LMs |
| ethics constraints | Things the user can't or won't do | E.g. no human-subject data without approval. **Hard** | no human-subject data |
| supervision | yes / limited / no | More risk is acceptable with a supervisor; influences the feasibility risk level | limited |
| other | Free text | Anything not covered above | "must finish before June" |

---

## 3. Investigation data: the request and the run

### 3.1 Investigation

**What it is:** one run of Eureka for one request: a single "evaluate my idea" or "find me an idea".

**Why it exists:**

- It is the container for everything a run produces.
- It holds the state the orchestrator needs to resume after a crash.
- It holds the information needed to reproduce and explain the result later: which inputs, which constraints, which versions of prompts and rules.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| id, user id | Identity and owner | Ownership, access control | `inv_204`, `u_17` |
| mode | **evaluate** (Mode 1) or **discover** (Mode 2) | Selects Workflow A or B in the orchestrator | evaluate |
| input | The raw user input as entered | Kept verbatim, so we can always show what the user asked, even after A1 restructures it | The idea text |
| complexity level | beginner / intermediate / advanced | The desired ambition; steers the Scout, Feasibility and Proposal | beginner |
| constraints snapshot | A **copy** of the user's profile at run time | If the user edits their profile later, old results must still be explained by the constraints that were actually used. A reference would silently rewrite history | (copy of 2.1) |
| status | created / clarifying / running / awaiting user / completed / failed / cancelled | The coarse lifecycle state for the UI and the resume logic | running |
| stage | The fine-grained state-machine step (doc 05), e.g. `light_evaluation` | Resume from the last completed stage; show progress. A stage counts as completed only when its output rows are committed in the same transaction | skeptic_round_2 |
| budget | Limits and counters: papers screened/read, rounds, refinements, cost, time | The orchestrator **reserves** budget before a stage and settles it after, so parallel candidate evaluations can't overspend | read 23/40, cost $1.10/$5 |
| cost so far, timings | Running totals | Fast budget checks (reconciled with the LlmCall rows), UI display | $1.10, 6 min |
| assumptions | Assumptions recorded when clarification was skipped | Honesty: the report lists what Eureka assumed instead of asking | "Assumed English-language QA benchmarks are acceptable" |
| config version | Versions of the prompts, rule tables and model settings used | Reproducibility: evaluation results must be tied to the exact configuration | `prompts v3, rules v2` |

### 3.2 Intake

**What it is:** A1's structured reading of the request, together with the user's corrections and answers from the clarification step.

- In **Mode 1** it holds the reading of the idea.
- In **Mode 2** it holds the field brief.

**Why it exists:**

- Free text can't be searched or judged reliably.
- The Intake is what the user sees and corrects at the clarification checkpoint, before money is spent.
- Keeping A1's original reading **and** the user's edits side by side shows what Eureka misunderstood. That's useful for improving A1.

*(Earlier versions had two entities, StructuredIdea and FieldBrief. Both are "A1's reading + the user's corrections", so they are one entity with mode-specific content. In Mode 1 the idea's title, question and facets also live on **Candidate v1**, created when the user confirms the Intake. That's the hand-off point where the user's idea becomes a candidate like any other.)*

**Common fields**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation id | Owner (exactly one Intake per investigation) | — | `inv_204` |
| agent reading | A1's output, unchanged | Kept to compare with the user's edits | (see the mode-specific content below) |
| user edits | What the user changed at clarification | The corrected version is what the run uses | "Languages: Georgian and Armenian" |
| clarifying questions + answers | ≤3 questions and the user's answers (or "skipped") | The human-in-the-loop checkpoint | Q: "Which languages?" A: "Georgian and Armenian" |
| assumptions | What A1 had to assume | Shown to the user; copied to the investigation if clarification is skipped | "Small = under 3B parameters" |
| confirmed at | When the user confirmed or skipped | The run starts only after this | — |

**Mode-specific content: Mode 1 (idea reading)**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| title | A short name for the idea | Display; later the proposal title | "RAG for hallucination reduction in low-resource QA with small LMs" |
| research question | One answerable question | Everything is judged against this question | "Does RAG reduce hallucination rates of small LMs in low-resource-language QA?" |
| facets | The idea broken into problem / method / data-setting / evaluation / contribution | Copied into Facet rows of Candidate v1; novelty is judged per facet | method: "retrieval-augmented generation" |
| vagueness flags | Specific weaknesses in the idea | Helps the user sharpen it; feeds the questions | "How hallucination is measured is unspecified" |

**Mode-specific content: Mode 2 (field brief)**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| field | The broad field chosen | Root of the exploration | NLP |
| subtopics (4–8) with why each fits | Narrower areas to explore, each with a reason | Landscape retrieval runs per subtopic; the reason makes it explainable | "LLM evaluation in low-resource languages: matches interests, public benchmarks exist" |
| landscape query plan | Queries per subtopic (recent work, surveys, highly cited, latest preprints) | Drives the landscape retrieval stage | "survey hallucination evaluation multilingual" |
| signal queries | Queries for GitHub / Hugging Face / news | Drives the signal service | "low-resource language QA dataset" |

### 3.3 ProgressEvent

**What it is:** one progress update during a run.

**Why it exists:** runs take minutes. Stored events feed the live progress view (over server-sent events) and let a reconnecting browser replay what it missed.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation | Owner | — | `inv_204` |
| sequence number | Order within the investigation | Replay: a browser asks for "everything after event 57" | 58 |
| time | When it happened | Display | — |
| stage | The current stage | The stage rail | skeptic_round_2 |
| message | Human-readable text | The activity feed | "Found 1 new relevant paper" |
| counters snapshot | Counts at this moment | Live counters, funnel chart | read 27 |
| cost so far | Spend at this moment | The live cost display | $1.40 |

---

## 4. Investigation data: the search trail

### 4.1 SearchQuery

**What it is:** one query sent to one source, either during the landscape phase (Mode 2) or for one specific evaluation.

**Why it exists:** the coverage snapshot must say what was searched, for which candidate, and by whom. Failed queries must be visible, not silently ignored.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation id | Owner | — | `inv_204` |
| evaluation id (optional) | The evaluation this search was for; empty = landscape/intake phase | In Mode 2, each candidate has its own targeted search. Without the scope, "how hard did we search for candidate 3?" can't be answered | `ev_31` |
| origin | analyst / landscape / scout / skeptic | Coverage by origin; measures the Skeptic's contribution | skeptic |
| query type | synonym / older term / facet-only / method-free / method-in-other-domain / … | The code check that enforces A1's query quotas; evaluates which query kinds find the important papers | method-free |
| round (Skeptic only) | Which Skeptic round issued it | Saturation is measured per round | 2 |
| source | Which API | Coverage per source | openalex |
| query text, filters | What was asked | Transparency; reproducibility | "hallucination small language models low-resource", year ≥ 2021 |
| result count | How many results came back | Detects dead queries | 34 |
| error | What failed, if anything | Failures appear in the coverage snapshot; source health monitoring | "429 rate limited (after retries)" |

### 4.2 RetrievalHit

**What it is:** one paper found within one **search scope** (the landscape phase, or one evaluation), with how it was found and how relevant it is *in that scope*.

**Why it exists:**

- Relevance belongs to the pair (scope, paper), not to the paper and not to the whole investigation. In Mode 2 the same paper can be highly relevant to candidate 3 and irrelevant to candidate 5.
- It feeds paper selection for reading, the paper library, and the coverage funnel.

*(Earlier versions had InvestigationPaper, which stored relevance per investigation. That's wrong once one investigation evaluates many candidates. Its "used by candidates" field was removed, because that can be derived from FacetMatch and ClaimEvidence.)*

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation id (optional) | The scope; empty = landscape phase | See above. A full evaluation that extends a light one also sees the light evaluation's hits, so they're never screened twice | `ev_31` |
| paper id | The global paper | — | `p_8812` |
| found by query IDs | Which queries surfaced it | Shows *how* it was found (analyst vs Skeptic); saturation per round uses the first finder | `q_12`, `q_40` |
| relevance (embedding) | Similarity score to the facets / idea | Ranking | 0.81 |
| relevance (LLM) | Screening score 0–3 + reason | Precision filtering; "relevant" for saturation means ≥2 | 3, "same task and method" |
| screened / selected for reading | Flags | Funnel counts in the coverage snapshot | selected |

---

## 5. Knowledge data (global, shared by all investigations)

### 5.1 Paper

**What it is:** one scholarly work, deduplicated across sources.

**Why it exists:**

- It is the single source of truth for a paper's identity and metadata.
- Agents cite papers only by their **internal id**, and references in reports are rendered from this record. That is how fabricated citations are prevented (doc 07).

**Merging versions:** an arXiv preprint and its later published version are **one Paper** carrying both IDs. Otherwise they'd be counted as two independent works.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| internal id | Eureka's own stable ID | Agents only ever see and cite this ID; stable even if external IDs change | `p_8812` |
| doi, arxiv id, semantic scholar id, openalex id | External identifiers (any may be missing; unique where present) | Dedup across sources; links in references; DOI verification | `arXiv:2405.01234` |
| title, abstract, year, venue, authors | Metadata from the sources | Display, ranking, embedding, abstract-level evidence | 2024, ACL |
| citation count | How often it is cited | A small ranking prior; shown in the UI | 57 |
| open-access PDF URL | Where legal full text can be fetched | Full-text stage | (arXiv PDF link) |
| tier | 1 = peer-reviewed, 2 = preprint (best available version) | Shown as a badge; reported in the coverage tier mix | 1 |
| embedding + embedding model id | Vector of title + abstract, and which model made it | Semantic ranking. The model ID prevents comparing vectors from different models | (vector), `specter2` |
| full text status | none / fetched / parsed / failed | Tells agents and the UI whether claims can come from full text or only the abstract | parsed |

### 5.2 Chunk

**What it is:** a passage of a paper's full text (about 800 tokens), with its section title.

**Why it exists:** agents can't read whole papers in every call. Chunks let retrieval send only the passages relevant to a question, and they are what full-text quotes point to.

**Chunks are immutable.** If the chunking method changes, a new set of chunks is created, and old quotes keep pointing at the old chunks.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| paper id | The paper it belongs to | Link back to the paper | `p_8812` |
| chunker version | Which chunking method produced it | Immutability: a new method creates new chunks instead of rewriting old ones | `chunker v1` |
| section title | E.g. "Limitations", "5 Experiments" | Limitations/future-work sections are prime Scout material; section context helps agents | "Limitations" |
| text | The passage | What agents read and quote from | — |
| position | Order within the paper | Rebuilding context; neighbouring chunks | 14 |
| embedding (+ model id) | Vector of the passage | Semantic passage search | — |
| keyword-search index | Full-text index | Hybrid search: exact terms (dataset names, acronyms) that vectors may miss | — |

### 5.3 PaperAnalysis

**What it is:** the Paper Reader's structured summary of a paper. It contains what the paper asks, does, finds and admits it didn't do.

**Why it exists:**

- **Idea-agnostic and reusable.** It's computed once and reused by every candidate and investigation. This is the biggest cost saving in Mode 2.
- It is the raw material for opportunity mining (limitations, future work, findings → contradictions, methods × tasks → combinations).

**Key:** one analysis per **(paper, extractor version, source level)**.

- When the Paper Reader prompt improves, papers are re-analysed instead of mixing formats.
- When full text becomes available later, a full-text analysis supersedes the abstract-level one without destroying it.
- Analyses are **single-flight**: if two investigations need the same paper at once, only one Paper Reader call runs.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| paper id, extractor version, model | Which paper, which prompt version, which model | Cache key; reproducibility | `p_8812`, `reader v2` |
| source level | full text or abstract | Part of the key. Honesty: an abstract-only analysis must never pretend to be complete | abstract |
| research questions, tasks, setting | What the paper investigates, and where | Facet matching (problem, setting) | "QA in Swahili and Yoruba" |
| methods | Techniques used | Facet matching (method); the method × problem matrix | "RAG with BM25 retriever" |
| datasets | Data used, named exactly as written | Feasibility (does the data exist?), dataset hints, facet matching | "AfriQA" |
| metrics, baselines | How results were measured, and against what | Proposal experiment design reuses them; facet matching (evaluation) | "exact match; closed-book baseline" |
| findings | Each: question/setting, claim, direction, quote ID | Contradiction mining needs comparable findings | "RAG improved EM by 8 points (direction: positive)" |
| limitations, future work, assumptions | Each with a quote ID | The Scout's core material; the Skeptic uses assumptions | "Only evaluated models ≥7B" |
| contribution type | Method / empirical study / dataset / survey / … | Significance comparison | empirical study |

### 5.4 Quote

**What it is:** an exact piece of text from a paper (its abstract or a chunk) that some statement relies on.

**Why it exists:** quotes are the atoms of trust. Every evidence claim must point to at least one quote, and each quote is **checked by code against the stored text**. Paraphrases and invented quotes are caught here.

**Quotes are global and deduplicated.** A quote is a fact about a paper's text, so verification happens once and every investigation benefits.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| paper id | Which paper | Link to the reference | `p_8812` |
| chunk id (empty = abstract) | Which passage | Source-level labelling; the UI shows context | `c_331` |
| exact text | The quoted words | What the user reads; what is verified | "we only evaluate models with at least 7B parameters" |
| text hash | A hash of the normalized text | Dedup key together with paper and chunk | — |
| source level | full text / abstract | The "based on abstract" badge | full text |
| verified | **System field.** Did the text match the source? | Unverified quotes are excluded from verdicts | true |
| verification score | **System field.** Similarity score of the match | Lets us tune the threshold (~0.95); diagnose near-misses | 0.99 |

---

## 6. Investigation data: where ideas come from

### 6.1 Signal (tier 3, Mode 2)

**What it is:** a piece of real-world information from GitHub, Hugging Face, Hacker News or the news. It suggests a practical problem or an emerging trend.

**Why it exists:** Mode 2 should find ideas that matter in practice, not just on paper. Signals are **stored separately from papers** and can only connect to candidates as inspiration (6.4). They have no path into evidence (doc 07).

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation id | Owner | — | `inv_301` |
| source | github / huggingface / hackernews / news | Display; source health | huggingface |
| URL, title | Where it is and what it's called | The user can open it | (dataset page) |
| summary | Short summary | Scout context | "Only 2 QA datasets for Georgian, both under 1k items" |
| date | When it was published or updated | Recency of the signal | 2026-08 |
| popularity metric | Stars, downloads, points | A rough importance hint | 1.2k downloads |
| topic tags | Which subtopic it relates to | Routing signals to the right part of the Scout's material | "low-resource QA" |

### 6.2 OpportunityItem (Mode 2)

**What it is:** one pattern that **code** mined from many PaperAnalysis records, prepared for the Scout.

**Why it exists:**

- The Scout shouldn't read 50 raw papers. It gets condensed, evidence-linked patterns instead.
- This is what makes Mode 2 "discovery from literature" instead of brainstorming. Every item is built from quotes, so it always reduces to real sentences in real papers.
- Mining can only run after the landscape papers have been analysed.

*(Previously named OpportunityMaterial.)*

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation id | Owner | — | `inv_301` |
| type | subtopic / limitation cluster / contradiction / combination / dataset | Different item types feed different Scout strategies | limitation cluster |
| label | Short description | Display; Scout context | "Only large models evaluated" |
| member quote IDs, paper IDs | The sentences and papers it was built from | Traceability; grounding | 5 quotes from 5 papers |
| counts | Type-specific numbers (see below) | Proves that "recurring" or "emerging" is real, not asserted | 5 papers, 4 author groups |

What each type contains:

| Type | Content | Example |
|---|---|---|
| Subtopic | Member papers, representative papers, publication counts per year | "Multilingual hallucination evaluation", 2022: 4, 2023: 11, 2024: 29 |
| Limitation cluster | Limitation/future-work quotes; distinct papers; distinct author groups | "Only large models evaluated": 5 papers, 4 groups |
| Contradiction | Two finding quotes, shared question/setting, check result | Paper A: RAG helps small models; Paper B: no gain under 1B |
| Combination | Method, task, row/column counts, neighbouring examples. A *hint*, never proof | "RAG × low-resource QA for <1B models: 0 papers in sample" |
| Dataset | Dataset name, tasks, availability result | "AfriQA: available, CC-BY" |

### 6.3 Candidate

**What it is:** a research idea under evaluation. It is either the user's own idea (Mode 1) or one created by the Scout (discovery, alternatives, refinements, replenishment).

**Why it exists:**

- It's the **unifying abstraction** that lets both modes share one pipeline.
- Its lineage records how an idea evolved, so the user sees *why* the final idea looks the way it does.

**Evaluated versions are frozen.** Once a version has an Evaluation, its title, question and facets never change. A change creates a new version.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| id, investigation id | Identity and owner | — | `cand_3` |
| version | Version number within its lineage | Display ("v2"); ordering | 2 |
| parent id | **Refinement** parent: this version *is* the parent, changed to fix a stated problem | The refinement history; refinement limits are counted along this chain | `cand_1` |
| spawned-from id | **Alternative** source: a *new* idea suggested because another failed | Kept separate from refinements, because alternatives start their own chain and their own refinement budget | `cand_1` |
| root id | The first version of this lineage | "All versions of this idea" and "refinements used so far" become single lookups | `cand_1` |
| origin | user / discover / alternative / refine / replenish | Where the idea came from; the UI shows it; evaluation compares origins | refine |
| origin strategy | limitation / future work / contradiction / combination / practical problem / refinement | How the Scout found it. Diversity checks; the UI badge | limitation |
| title, research question, hypothesis | The idea itself | What is evaluated and finally proposed | RQ: "Does RAG reduce hallucination of sub-1B models in Georgian QA?" |
| motivation | Why it's worth doing (text). The links to its sources are CandidateInspiration rows | Shown on the card; judged later | "Five papers report evaluating only ≥7B models…" |
| potential contribution (initial) | The Scout's claim of what this adds | A *hypothesis* to be judged by A6, never trusted as-is | "First evaluation of RAG for sub-1B models in Georgian" |
| complexity fit, constraint notes | The Scout's own estimate of fit | A hypothesis checked by Feasibility; also a pre-screen hint | "T1, 8 weeks, public data" |
| change note | For refinements: what changed and why | The user understands the refinement; tests check it addresses the reason | "Reduced to one language and 2 models to fit T1" |
| status | proposed / screened out / light evaluating / needs refinement / advanced / full evaluating / finalist / rejected / selected / proposal ready | The lifecycle (doc 05). Changes only if the current status matches what the orchestrator expects, so parallel workers can't both move one candidate | finalist |
| current evaluation id | The evaluation the UI shows: the latest completed one, preferring full over light | One definition of "the current result" for every screen | `ev_31` |
| rejection reason | Summary of the rejecting TriageDecision | Rejections are shown openly | "Rule 3: previously investigated (p_9001)" |

*"Signal-only" is not stored.* It's derived: a candidate is signal-only when none of its inspirations resolves to a verified tier-1/2 quote. Storing it could let it drift out of sync.

### 6.4 CandidateInspiration

**What it is:** one link from a candidate to something that inspired it: **exactly one** of a quote, an opportunity item, or a signal.

**Why it exists:**

- It makes the Scout's grounding rule checkable by code: *at least two inspirations must resolve to verified quotes from tier-1/2 papers.* Candidates that fail are screened out before any money is spent.
- It powers "inspired by" in the UI.

A Mode 1 user idea has no inspirations; it is the user's own.

*(New in version 2. Earlier versions only mentioned "inspiration links" inside the motivation field.)*

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| candidate id | Owner | — | `cand_3` |
| kind | quote / opportunity item / signal | Says which link is set; exactly one must be | opportunity item |
| quote id / opportunity item id / signal id | The linked source | The traceable inspiration | `oi_7` (limitation cluster) |
| role | motivation / gap hint / practical need | How the Scout used it | gap hint |
| note | One line on how it inspired the idea | Readable "inspired by" text | "Recurring limitation across 5 papers" |

### 6.5 Facet

**What it is:** one component of a candidate's idea.

**Why it exists:** novelty is rarely all-or-nothing. A paper may share the problem but not the method. Facets make "what exactly is new" precise, and they power the prior-work matrix.

Facets belong to a specific candidate version and are frozen with it.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| candidate | Owner (a specific version) | — | `cand_3` |
| kind | problem / method / data-setting / evaluation / contribution | Matching rules treat facet kinds differently | data-setting |
| text | The facet description | What papers are matched against | "Georgian-language open-domain QA" |
| is-key flag | Whether this facet is essential to the idea's novelty | The verdict rules look at the *key* combination | true |

---

## 7. Investigation data: judging a candidate and concluding

### 7.1 Evaluation (with coverage snapshot)

**What it is:** one evaluation pass of a candidate version, at a given depth, together with the frozen record of how thorough it was.

**Why it exists:**

- A candidate can be evaluated more than once: light, then upgraded to full, or re-evaluated after a stage failure. Keeping each pass separate keeps results honest about depth. A light evaluation can never yield "Supported opportunity".
- **The coverage snapshot** turns absence of evidence into an honest, qualified statement. "We found nothing" means very different things after 20 papers vs 300. The verdict rules use its numbers.

**Order at the end of an evaluation:**

1. Freeze the coverage snapshot.
2. Compute the verdicts.
3. Run triage.

*(Earlier versions had a separate CoverageReport entity. There's always exactly one per evaluation and it's never queried on its own, so it's part of the Evaluation.)*

**Evaluation fields**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| id, candidate id | Identity; which candidate version | — | `ev_31`, `cand_3` |
| extends id | The light evaluation this full evaluation upgrades | Makes reuse explicit: the full pass inherits the light pass's retrieval hits and claims | `ev_22` |
| depth | light / full | Verdict thresholds differ by depth | full |
| started / finished | Timing | Latency analysis, UI | — |
| counters | Queries, papers screened/read, Skeptic rounds | Budgets; input to the snapshot | 280 screened, 31 read, 3 rounds |
| status | running / done / partial / failed | Partial results are allowed but labelled | done |

**Coverage snapshot fields** (computed from the search trail and challenges, then frozen)

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| sources and failures | Which sources were queried, which failed | Blind spots; verdict downgrades | arXiv timed out twice |
| query counts by origin | Analyst / Scout / Skeptic | Shows how wide the search was | 18 / 0 / 14 |
| date range | Years covered | Known limits | 2015–2026 |
| paper funnel | Retrieved → deduplicated → screened → read | Thresholds for verdicts; UI funnel | 620 → 410 → 280 → 31 |
| full text vs abstract | Of the papers read | Evidence depth | 9 full / 22 abstract |
| tier mix | Peer-reviewed vs preprint | Quality context | 18 / 13 |
| Skeptic saturation curve | New relevant papers per round (measured from data, not asserted by the Skeptic) | Required for "Supported" | 3 → 1 → 0 |
| budgets hit | Which limits stopped the work | Honest: "stopped by budget" | none |
| blind spots | Known gaps in coverage | Required in every report | "No PubMed; English only; cache from 2026-11-01" |

### 7.2 FacetMatch

**What it is:** one cell of the prior-work matrix: how one paper relates to one facet of the candidate, within one evaluation.

**Why it exists:** it's the most important input for the novelty verdict and the most inspectable part of the UI. A user can see exactly which paper covers which part of the idea, with a quote.

The facet and the evaluation must belong to the same candidate version.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation, facet, paper | Which cell (unique together) | — | `ev_31` × method × `p_8812` |
| overlap | same / similar / different / not addressed / unknown | The verdict engine counts "same" on key facets | same |
| quote ID | The evidence for the label | Without a verified quote, "same" doesn't count | `qt_77` |
| rationale | A short explanation | The user reads why; the Skeptic can challenge it | "Also uses BM25-based RAG" |

### 7.3 Claim and ClaimEvidence

**What it is:**

- A **claim** is a single statement that Eureka's reasoning relies on.
- **ClaimEvidence** links a claim to a quote, with a stance.

**Why it exists:**

- This is the core of the evidence ledger. Every conclusion breaks down into claims, and claims into quotes.
- Separating the claim from its evidence lets one quote support several claims, and one claim have both supporting and contradicting evidence.

**Evidence can only be a Quote.** Signals are in a different table and can't be linked here, so "signals are never evidence" holds by structure.

**Agents write claim text; only code moves claim status.** Claim text is never edited; a revised position is a new claim.

**Claim fields**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation | Owner | — | `ev_31` |
| kind | prior work / gap / significance / feasibility / limitation / contradiction | Verdict rules use specific kinds; the UI groups by kind | gap |
| text | The statement | What the user reads | "No retrieved study evaluates RAG for sub-1B models in Georgian QA" |
| statement type | evidence / interpretation / hypothesis | Honest labelling in the UI (doc 07) | interpretation |
| inferred flag | The agent concluded it, rather than a source stating it | Gap claims are usually inferred; the UI must say so | true |
| status | **System field:** proposed → verified / unverified quote / no evidence → challenged → defended / refuted | Only verified or defended claims count in verdicts | challenged |
| author agent | Which agent made it | Debugging; conflict resolution rules | novelty |

**ClaimEvidence fields**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| claim, quote | The link | — | — |
| stance | supports / contradicts / qualifies | Shows both sides; contradicting evidence feeds the Skeptic and the limitations | contradicts |

### 7.4 Challenge

**What it is:** one objection raised by the Skeptic.

**Why it exists:**

- Self-criticism must be recorded, not just "done".
- Challenges change verdicts by rule, and unresolved ones go into the report's limitations.
- The Skeptic log in the UI is built from these rows. Rounds are append-only: earlier challenges are never deleted.

The papers the Skeptic finds flow back into the search trail as SearchQuery (origin skeptic) and RetrievalHit rows in this evaluation's scope. That is what lets the Novelty Investigator's revision see them.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation, round | Where and when | Saturation tracking per round | round 2 |
| target claim | The claim being attacked | Conflict resolution needs the link | the gap claim above |
| type | prior work found / weak assumption / methodological flaw / overstated contribution / alternative explanation / feasibility risk | Different types are handled by different rules | prior work found |
| argument | The Skeptic's reasoning | The user reads it | "A 2025 workshop paper tests RAG on 0.5B models for Georgian" |
| evidence quote IDs, new paper IDs | The proof and the newly found papers | A challenge backed by a verified quote can override a novelty claim | `qt_102`, `p_9001` |
| resolution | accepted / rebutted / unresolved | Shows how the system responded; unresolved challenges go into the limitations | accepted |
| rebuttal claim id | The Novelty Investigator's answer, as a claim with its own evidence | The debate is data: claim → challenge → rebuttal → evidence | `cl_14` |

### 7.5 Verdict

**What it is:** the official conclusion for one dimension of an evaluation.

**Why it exists:**

- It's what the user sees first.
- It is **computed by code** from structured inputs, so it is consistent and explainable (the rule trace).
- The rule-table version makes it reproducible when the rules change.
- Verdicts are immutable once the evaluation completes.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation | Owner (one verdict per dimension) | — | `ev_31` |
| dimension | novelty / significance / feasibility / recommendation | The recommendation is computed from the other three. A quick feasibility pre-screen produces only a feasibility verdict | novelty |
| category | The verdict, e.g. Potential opportunity, Moderate, Feasible with adjustments, Pursue | What the UI shows | Incremental variation |
| rule trace | Rule IDs that fired + the claim IDs behind them | The "why": the user can drill down to quotes | N2 fired: claims `cl_5`, `cl_9` |
| uncertainties | What remains unknown | Required honesty (doc 07 §6) | "Closest paper available as abstract only" |
| blocking requirements | Feasibility only: what makes it impossible as-is | Drives refinement (simplify) and the user's decision | "Needs a T2 GPU for fine-tuning" |
| rule-table version | Which version of the rules produced it | Reproducibility when rules change | rules v2 |

### 7.6 TriageDecision

**What it is:** a record of what the orchestrator decided about a candidate, and why. That's after an evaluation, or at pre-screen before any evaluation.

**Why it exists:**

- Iteration (refine / reject / advance) must be explainable and auditable. These rows power the reasons on the candidate cards and let us debug loops.
- Loop limits are enforced *when the decision is written*: the refinement count along `root_id` and the investigation budget are checked before a `refine` decision creates a new candidate.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| candidate, evaluation | What was triaged; the evaluation is empty for pre-screen decisions | Pre-screen rejections are recorded too, so no rejection is silent | `cand_1`, `ev_22` |
| rule number | Which triage rule matched (doc 05 §4) | Explainability; tests | 5 |
| action | advance / refine / reject / expand search / finalize / screen out | What happened next | refine |
| evidence IDs | The verdicts/claims behind the decision | Drill-down | blocking requirement in `vd_12` |
| created candidate id | The new version created by a `refine` decision | Closes the loop: the UI draws "refined from … because …" | `cand_3` |
| timestamp | When | Ordering in the UI timeline | — |

### 7.7 ResearchPackage

**What it is:** the final deliverable for a strong candidate: the 17 sections from doc 02.

**Why it exists:** this is what the student takes away. Storing it structurally (not as one blob of text) lets us tag each section's evidence status, regenerate one section at a time, and render references safely from the DB.

A package is grounded in **one specific evaluation**. If the candidate is re-evaluated, an older package still points to the evidence it was written from.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| candidate, evaluation | What it's about, and the evidence it's built on | Traceability to exactly one evidence set | `cand_3`, `ev_31` |
| revision | Revision number (regenerating a section creates a new revision) | History; comparing revisions | 2 |
| sections (17) | Each with content, statement-type tags and referenced claim/paper IDs | Honest labelling per section; references are IDs that the backend turns into citations after checking they exist | Section 13: experiment design |
| generation model, prompt version | How it was produced | Reproducibility; evaluation | `proposal v1` |
| export history | When and in which format it was exported | Convenience; auditing | md, 2026-11-20 |

---

## 8. Operational data

| Entity | What it is | Why it exists | Key fields (meaning) |
|---|---|---|---|
| **LlmCall** | One call to Claude | Cost accounting, budget enforcement, debugging, and tying evaluation results to prompt versions | investigation (empty for evaluation-harness runs); evaluation (optional: cost per candidate); agent role; model and effort (the configuration used); prompt version; input / output / cache-read tokens (cost and caching effectiveness); cost; latency; API request ID (for Anthropic support); stop reason (detects refusals and truncation); error |
| **ApiCache** | Stored responses from external APIs | Cheaper, faster, rate-limit-safe and repeatable runs (demo safety). Not the source of truth; the Paper table is | source; normalized request key; response; fetched at; time-to-live |

### 8.1 Deferred to later versions

| Entity | Purpose when added | Why not in the MVP |
|---|---|---|
| **ConversationMessage** | Messages of the optional "Ask about this investigation" panel | Clarification answers live in Intake; Mode 2 steering actions are recorded as Scout tasks / TriageDecisions |
| **ToolCall** | A detailed log of every Scout/Skeptic tool call | Their searches are already SearchQuery rows (origin scout/skeptic); tool budgets are counters on the investigation; other tool calls go to structured logs |

---

## 9. From agent output to database rows (the bridge to doc 08)

An agent's output schema is **not** the same as a database table. The LLM fills only the "judgment" fields; code adds identity, links and trust fields.

Output shape names (StructuredIdea, FieldBrief, …) are the *agent contracts* from doc 04. The right-hand column shows where their content is stored.

| Agent → output shape | What the LLM fills | What code adds afterwards | Stored as |
|---|---|---|---|
| A1 → StructuredIdea | Title, question, facets (kind, text, is-key), assumptions, vagueness flags, questions | IDs, the user's answers and edits | Intake (agent reading) → on confirmation: Candidate v1 (origin user) + Facets |
| A1 → FieldBrief | Subtopics + reasons, query plan, signal queries, questions | IDs, the user's edits | Intake (agent reading) → SearchQuery rows when executed |
| A1 → QueryPlan | Queries with their type (synonym, facet-only, method-free, …) | **Quota check** (regenerate if missing), scope, execution, result counts | SearchQuery |
| A2 → CandidateSet | Per candidate: title, question, hypothesis, origin strategy, motivation, inspirations (quote text + paper ID, or item/signal ID), contribution, facets, fit notes, change note | IDs; version / parent / spawned-from / root; origin; **quote verification; grounding check (≥2 verified tier-1/2 quotes); dedup; hard-constraint filter**; status | Candidate + Facet + CandidateInspiration + Quote; pre-screen results as TriageDecision |
| A3 → PaperAnalysis | All extracted fields, with quotes (text + chunk ID) | Paper ID, extractor version, model, **source level, quote verification** | PaperAnalysis + Quote |
| A4 → NoveltyAssessment | Facet matches (label, quote, rationale), closest works, claims (text, kind, statement type, inferred, quotes with stance), proposed category, uncertainties, challenge responses | **Quote verification, claim status, the verdict category (the proposed one is only advisory)**, rebuttal links on challenges | FacetMatch, Claim, ClaimEvidence, Quote; Challenge resolution; Verdict computed by code |
| A5 → ChallengeSet | Challenges (target, type, argument, quotes, new paper IDs), its own saturation estimate | **Paper ID existence check, quote verification**, round number; saturation **measured** from RetrievalHits (the Skeptic's estimate is advisory) | Challenge + SearchQuery (origin skeptic) + RetrievalHit |
| A6 → QuickFeasibility | Level, blocking requirements, simplifications | **Hard-constraint check** (code can override to "not feasible") | Verdict (feasibility only) + TriageDecision |
| A6 → Significance + Feasibility assessments | Rubric answers with claims and quotes, beneficiaries, timeline, datasets, risks, simplifications | **Dataset lookup results attached, quote verification, verdict categories by rule** | Claim, ClaimEvidence, Verdict |
| A7 → ResearchPackage | Section contents with statement-type tags and referenced IDs | **Reference rendering from DB records, a check that every ID exists**, revision number, evaluation link | ResearchPackage |

**Practical consequence for prompt writing:** each agent's prompt explains its output shape field by field, using the "Meaning" and "Why we need it" columns in this document. A model that understands *why* a field exists fills it better.

---

## 10. Important constraints and indexes

| Rule | Why |
|---|---|
| Paper external IDs are unique where present | Dedup: the same paper from two sources becomes one record (get-or-create with retry under concurrency) |
| PaperAnalysis is unique per (paper, extractor version, source level) | The cache works; formats never mix; full-text analyses can supersede abstract ones |
| Quote is unique per (paper, chunk, text hash) | Verify once, reuse everywhere |
| RetrievalHit is unique per (scope, paper); FacetMatch per (evaluation, facet, paper); Verdict per (evaluation, dimension) | No duplicate judgments |
| Vector indexes on paper and chunk embeddings; keyword index on chunks | Fast semantic + exact-term (hybrid) search |
| CandidateInspiration has exactly one target set | Keeps the grounding rule checkable |
| Evaluated candidate versions, their facets, chunks, quote text, verdicts and coverage snapshots are immutable | Explanations must never change under the user's feet |
| Candidates keep their full lineage; nothing is deleted on refinement | The user (and evaluation) can see how ideas evolved and why |
| Quotes can only reference existing papers/chunks; ClaimEvidence can only reference quotes | Structural protection against invented sources and against signals as evidence |
| The constraints snapshot and config version are stored on the investigation | Every result stays reproducible and explainable later |
| Deleting an investigation cascades to its own rows, never to the global library | User privacy without destroying shared knowledge |

---

## 11. Walk-through: what gets written during one Mode 1 run

Using the running example:

1. **The user submits the idea** → `Investigation` (mode evaluate, beginner, constraints snapshot), with status *created*.
2. **A1 runs** → `Intake` (agent reading: title, question, 5 facets, 2 vagueness flags, 1 question). Status *clarifying*.
3. **The user answers** "Georgian and Armenian" and confirms → `Intake` (user edits, answers, confirmed at) and `Candidate` v1 (origin user, root = itself) with 5 `Facet` rows. Status *running*.
4. **A full `Evaluation` starts** (`ev_22`). A1's query plan → 18 `SearchQuery` rows scoped to `ev_22`. Retrieval creates/updates global `Paper` rows and `RetrievalHit` rows (scope `ev_22`).
5. **Full text** for 9 open-access papers → `Chunk` rows.
6. **A3 reads 31 papers.** Most `PaperAnalysis` rows are new; 6 were already cached from an earlier investigation and reused at no cost. Their quotes are verified → `Quote` rows.
7. **A4** → 5 × 25 `FacetMatch` cells, 12 `Claim` rows with `ClaimEvidence`.
8. **Skeptic round 1** → 4 `Challenge` rows, 14 more `SearchQuery` rows (origin skeptic, round 1), 3 new `RetrievalHit` rows. A4 accepts one challenge (a 2025 paper is "same" on method and setting).
9. **Rounds 2–3** → the measured saturation is 3 → 1 → 0. The coverage snapshot in `ev_22` is frozen.
10. **A6** → significance and feasibility claims. The **Verdict engine** writes `Verdict` rows: novelty *Incremental variation*, significance *Moderate*, feasibility *Feasible with adjustments*, recommendation *Refine*.
11. **Triage** → `TriageDecision` (rule 4, refine, created candidate `cand_3`). The Scout writes `Candidate` v2 (`cand_3`: parent `cand_1`, root `cand_1`, change note "focus on sub-1B models and Armenian, where the 2025 paper didn't test"). A light re-check → `Evaluation` `ev_31`, then `Verdict` rows (novelty *Potential opportunity*, recommendation *Pursue*). `cand_3`'s current evaluation = `ev_31`.
12. **The user requests a proposal** → `ResearchPackage` (candidate `cand_3`, evaluation `ev_31`, revision 1).

Throughout the run: `ProgressEvent` rows (with sequence numbers) stream to the UI; every model call writes an `LlmCall`; repeated API requests hit `ApiCache`.

**In Mode 2:**

- The Intake holds a field brief.
- Landscape `SearchQuery` and `RetrievalHit` rows have no evaluation scope.
- `Signal` and `OpportunityItem` rows are written before generation.
- The Scout creates 8–12 `Candidate` rows with `CandidateInspiration` links; pre-screen rejections become `TriageDecision` rows (screen out).
- Each light/full evaluation has its own scoped search trail.

---

## 12. Agent output shapes (summary)

These are implemented later as schemas, one per agent output:

| Shape | Producer |
|---|---|
| StructuredIdea, FieldBrief, QueryPlan | A1 |
| CandidateSet | A2 |
| PaperAnalysis | A3 |
| NoveltyAssessment (facet matrix, closest works, claims, proposed category, uncertainties, challenge responses) | A4 |
| ChallengeSet (challenges + saturation estimate) | A5 |
| QuickFeasibility; SignificanceAssessment + FeasibilityAssessment | A6 |
| ResearchPackage | A7 |

All of them reference papers by internal ID only and carry quotes as text + paper ID + chunk ID, ready for the verifier.
