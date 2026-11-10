# 10: Data Model

The data model has two jobs. It is the **database schema** (PostgreSQL + pgvector), and it defines the **structured shapes** that agents output (implemented later as Pydantic models used as ADK output schemas).

This document describes entities in plain tables. It deliberately contains no code.

**Why read this before the LLM engineering doc:** every LLM agent in Eureka has one job, which is to fill in a specific data shape. Once you know what each entity means and why each field exists, each agent's prompt becomes an explanation of how to fill that shape well. Section 7 connects each agent's output to the tables it ends up in.

---

## 0. How to read this document

### 0.1 Four kinds of data

| Kind | What it is | Lifetime | Examples |
|---|---|---|---|
| **User data** | Who the user is and what they can do | Long-lived, editable | User, ConstraintsProfile |
| **Knowledge data (global)** | Facts about the scientific world, shared by all investigations | Long-lived, reused, grows over time | Paper, Chunk, PaperAnalysis, Quote |
| **Investigation data** | Everything produced by one run of Eureka | Belongs to one investigation; deleted with it | Investigation, Candidate, Claim, Verdict, … |
| **Operational data** | How the system behaved (cost, errors, cache) | Kept for monitoring and evaluation | LlmCall, ToolCall, ApiCache |

The split between **global knowledge** and **investigation data** is the most important design choice in this document:

- A paper and its analysis are the same whoever asks about them, so they are stored once and reused. This is what makes Mode 2 affordable.
- A *judgment* about a paper (e.g. "this paper does the same thing as candidate 3") depends on the candidate, so it belongs to the investigation.

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

### 1.1 Who writes and who reads each entity

| Entity | Written by | Read by |
|---|---|---|
| User, ConstraintsProfile | User (UI) | Scout, Value & Feasibility, Proposal, orchestrator (hard-constraint filter) |
| Investigation | API (create), orchestrator (status/stage/counters) | Everything |
| StructuredIdea / FieldBrief | A1 Idea Analyst (+ user edits at clarification) | Orchestrator (query plans), Scout, Novelty |
| SearchQuery | Orchestrator, Scout tools, Skeptic tools | Coverage report, UI |
| Paper, Chunk | Literature Discovery service | All agents (through context packets and tools) |
| PaperAnalysis | A3 Paper Reader | Novelty, Skeptic, Scout (via mining), Proposal |
| Quote | A3 (extraction) / A4 / A5 / A2; **verified by C4** | Claims, UI evidence chips |
| InvestigationPaper | Retrieval + ranking | Paper library UI, coverage |
| Signal | Signal service | Scout only |
| OpportunityMaterial | Mining code | Scout |
| Candidate | A2 Scout / user (Mode 1); status set by the orchestrator | Everything downstream |
| Facet | A1 (normalization) | Novelty, Skeptic, retrieval scoring |
| Evaluation | Orchestrator | UI, triage |
| FacetMatch, Claim | A4 Novelty (+ A6, A5 for some claims) | Verdict engine, UI |
| ClaimEvidence | Agents propose; verifier confirms | Verdict engine, UI |
| Challenge | A5 Skeptic; resolution by A4 + rules | Verdict engine, UI Skeptic log |
| Verdict | **C4 Verdict engine (code)** | Triage, UI, Proposal |
| CoverageReport | Orchestrator + retrieval counters | Verdict engine, UI, report |
| TriageDecision | Orchestrator (rules) | UI ("why"), lineage |
| ResearchPackage | A7 Proposal Designer; references rendered by the backend | UI, export |
| ConversationMessage | User + Ask panel | Ask panel |
| ProgressEvent | Orchestrator | UI progress view |
| LlmCall, ToolCall | ADK callbacks, tool wrappers | Cost/budget control, monitoring, evaluation |
| ApiCache | Source adapters | Source adapters |

---

## 2. User data

### 2.1 User

**What it is:** a person using Eureka.

**Why it exists:** to own investigations and profiles, and to enforce per-user rate limits and cost caps, so one user can't spend the whole budget.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| user id | Internal unique identifier | Every other user-owned row points to it | `u_17` |
| email | Login identity | Authentication and contact | (from the auth provider) |
| created at | When the account was created | Basic auditing | 2026-11-10 |

### 2.2 ConstraintsProfile

**What it is:** the user's real situation: what they know, how much time and compute they have, and what they must avoid.

**Why it exists:**

- Feasibility is meaningless without it ("feasible for whom?").
- It also steers the Scout away from ideas the user can't execute.

It is stored once and reused, so users don't re-enter it on every run.

**Hard vs soft:**

- Hard fields (compute, time, data access, ethics) are enforced by code filters.
- Soft fields (skills, preferences) are weighed by the LLM.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| academic level | undergraduate / master / phd / researcher | Sets the expected depth and the default ambition; the Proposal Designer adapts its language and scope | undergraduate |
| time available | Total weeks + hours per week | Feasibility estimates a timeline per phase and compares it with this. **Hard constraint** | 12 weeks, 10 h/week |
| skills | List + free text of what the user can already do | Feasibility flags skill gaps; the Scout prefers methods the user knows | Python, basic PyTorch, pandas |
| background | Courses taken, prior projects | Helps judge how fast missing skills can be learned | "Intro ML course, one NLP class project" |
| compute tier | T0 laptop / T1 free notebook GPU / T2 one GPU for days / T3 cluster, + free text | The most common reason an idea is infeasible. **Hard constraint** | T1, "free notebook GPU" |
| data access | public only / can collect / institutional | Decides whether a candidate that needs new data is possible. **Hard constraint** | public only |
| budget | none / small / some | Paid APIs, annotation, cloud costs | none |
| preferred technologies, subfields | Lists | Steer the Scout and query planning (soft) | Hugging Face, small LMs |
| ethics constraints | Things the user can't or won't do | E.g. no human-subject data without approval. **Hard constraint** | no human-subject data |
| supervision | yes / limited / no | More risk is acceptable with a supervisor; influences the feasibility risk level | limited |
| other | Free text | Anything not covered above | "must finish before June" |

---

## 3. Investigation data: the run itself

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
| input | The raw user input as entered | Kept verbatim so we can always show what the user asked, even after A1 restructures it | The idea text |
| complexity level | beginner / intermediate / advanced | The desired ambition; steers the Scout, Feasibility and Proposal | beginner |
| constraints snapshot | A **copy** of the profile at run time | If the user edits their profile next week, old results must still be explained by the constraints that were actually used | (copy of 2.2) |
| status | created / clarifying / running / awaiting user / completed / failed / cancelled | The coarse lifecycle state for the UI and the resume logic | running |
| stage | The fine-grained state-machine step (doc 05), e.g. `light_evaluation` | Resume from the last completed stage; show progress | skeptic_round_2 |
| budgets | Limits and counters: papers screened/read, rounds, cost, time | The orchestrator checks them before every stage; they stop endless loops | read 23/40, cost $1.10/$5 |
| cost so far, timings | Running totals | UI display, cost caps, evaluation analysis | $1.10, 6 min |
| assumptions | Assumptions recorded when clarification was skipped | Honesty: the report lists what Eureka assumed instead of asking | "Assumed English-language QA benchmarks are acceptable" |
| config version | Versions of the prompts, rule tables and model settings used | Reproducibility: evaluation results must be tied to the exact configuration | `prompts v3, rules v2` |

### 3.2 StructuredIdea (Mode 1)

**What it is:** A1's precise, structured reading of the user's free-text idea.

**Why it exists:** free text can't be searched, compared or judged reliably. Breaking the idea into facets is what makes facet-level novelty checks possible (doc 07). The user can see and correct this reading at the clarification step.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| title | A short name for the idea | Display, and later the proposal title | "RAG for hallucination reduction in low-resource QA with small LMs" |
| research question | One answerable question | Everything is judged against this question | "Does RAG reduce hallucination rates of small LMs in low-resource-language QA?" |
| facets | The idea broken into problem / method / data-setting / evaluation / contribution | Novelty is judged per facet; retrieval scores papers against each facet | method: "retrieval-augmented generation" |
| assumptions | What A1 had to assume | Shown to the user for correction | "Small = under 3B parameters" |
| vagueness flags | Specific weaknesses in the idea | Helps the user sharpen it; feeds the questions | "How hallucination is measured is unspecified" |
| clarifying questions + answers | ≤3 questions and the user's answers | The human-in-the-loop checkpoint | Q: "Which languages?" A: "Georgian and Armenian" |

### 3.3 FieldBrief (Mode 2)

**What it is:** A1's plan for exploring a field: which subtopics, and why they suit this user.

**Why it exists:** "NLP" is too broad to search. The brief makes landscape retrieval focused and personalized, and the user can edit it before money is spent.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| field | The broad field chosen | Root of the exploration | NLP |
| subtopics (4–8) with why each fits | Narrower areas to explore, each with a reason | Landscape retrieval runs per subtopic; the reason makes it explainable | "LLM evaluation in low-resource languages: matches interests, public benchmarks exist" |
| landscape query plan | Queries per subtopic (recent work, surveys, highly cited, latest preprints) | Drives the landscape retrieval stage | "survey hallucination evaluation multilingual" |
| signal queries | Queries for GitHub / Hugging Face / news | Drives the signal service | "low-resource language QA dataset" |
| clarifying questions + answers | As in StructuredIdea | Same checkpoint | "Any preferred languages?" |

---

## 4. Knowledge data (global, shared by all investigations)

### 4.1 Paper

**What it is:** one scholarly work, deduplicated across sources.

**Why it exists:**

- It is the single source of truth for a paper's identity and metadata.
- Agents cite papers only by their **internal id**, and references in reports are rendered from this record. That is how fabricated citations are prevented (doc 07).

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| internal id | Eureka's own stable ID | Agents only ever see and cite this ID; stable even if external IDs change | `p_8812` |
| doi, arxiv id, semantic scholar id, openalex id | External identifiers (any may be missing) | Dedup across sources; links in references; DOI verification | `arXiv:2405.01234` |
| title, abstract, year, venue, authors | Metadata from the sources | Display, ranking, embedding, abstract-level evidence | 2024, ACL |
| citation count | How often it is cited | A small ranking prior; shown in the UI | 57 |
| open-access PDF URL | Where legal full text can be fetched | Full-text stage | (arXiv PDF link) |
| tier | 1 = peer-reviewed, 2 = preprint | Shown as a badge; reported in the coverage tier mix | 2 |
| embedding + embedding model id | Vector of title + abstract, and which model made it | Semantic ranking. The model ID prevents mixing vectors from different models | (vector), `specter2` |
| full text status | none / fetched / parsed / failed | Tells agents and the UI whether claims can come from full text or only the abstract | parsed |

### 4.2 Chunk

**What it is:** a passage of a paper's full text (about 800 tokens), with its section title.

**Why it exists:** agents can't read whole papers in every call. Chunks let retrieval send only the passages relevant to a question, and they are what full-text quotes point to.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| paper id | The paper it belongs to | Link back to the paper | `p_8812` |
| section title | E.g. "Limitations", "5 Experiments" | Limitations/future-work sections are prime Scout material; section context helps agents | "Limitations" |
| text | The passage | What agents read and quote from | — |
| position | Order within the paper | Rebuilding context; neighbouring chunks | 14 |
| embedding (+ model id) | Vector of the passage | Semantic passage search | — |
| keyword-search index | Full-text index | Hybrid search: exact terms (dataset names, acronyms) that vectors may miss | — |

### 4.3 PaperAnalysis (global, cached per paper + extractor version)

**What it is:** the Paper Reader's structured summary of a paper. It contains what the paper asks, does, finds and admits it didn't do.

**Why it exists:**

- **Idea-agnostic and reusable.** It's computed once per paper and reused by every candidate and investigation. This is the biggest cost saving in Mode 2.
- It is the raw material for opportunity mining (limitations, future work, findings → contradictions, methods × tasks → combinations).
- The **extractor version** means that when we improve the Paper Reader prompt, we can re-analyse papers instead of mixing old and new formats.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| paper id, extractor version, model | Which paper, which prompt version, which model | Cache key; reproducibility | `p_8812`, `reader v2` |
| source level | full text or abstract | Honesty: an abstract-only analysis must never pretend to be complete | abstract |
| research questions, tasks, setting | What the paper investigates, and where | Facet matching (problem, setting) | "QA in Swahili and Yoruba" |
| methods | Techniques used | Facet matching (method); the method × problem matrix | "RAG with BM25 retriever" |
| datasets | Data used, named exactly as written | Feasibility (does the data exist?), dataset hints, facet matching | "AfriQA" |
| metrics, baselines | How results were measured, and against what | Proposal experiment design reuses them; facet matching (evaluation) | "exact match; closed-book baseline" |
| findings | Each: question/setting, claim, direction, quote ID | Contradiction mining needs comparable findings | "RAG improved EM by 8 points (direction: positive)" |
| limitations, future work, assumptions | Each with a quote ID | The Scout's core material; the Skeptic uses assumptions | "Only evaluated models ≥7B" |
| contribution type | Method / empirical study / dataset / survey / … | Significance comparison | empirical study |

### 4.4 Quote

**What it is:** an exact piece of text from a paper (its abstract or a chunk) that some statement relies on.

**Why it exists:** quotes are the atoms of trust. Every evidence claim must point to at least one quote, and each quote is **checked by code against the stored text**. Paraphrases and invented quotes are caught here.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| paper id | Which paper | Link to the reference | `p_8812` |
| chunk id (empty = abstract) | Which passage | Source-level labelling; the UI shows context | `c_331` |
| exact text | The quoted words | What the user reads; what is verified | "we only evaluate models with at least 7B parameters" |
| source level | full text / abstract | The "based on abstract" badge | full text |
| verified | **System field.** Did the text match the source? | Unverified quotes are excluded from verdicts | true |
| verification score | **System field.** Similarity score of the match | Lets us tune the threshold (~0.95); diagnose near-misses | 0.99 |

### 4.5 InvestigationPaper

**What it is:** the link between a global paper and one investigation, recording how this investigation found it and used it.

**Why it exists:** the same paper can be highly relevant in one investigation and irrelevant in another. Relevance belongs to the investigation, not to the paper. This entity also feeds the paper library and the coverage counts.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation id, paper id | The link | — | `inv_204`, `p_8812` |
| found by query IDs | Which queries surfaced it | Shows *how* it was found (analyst vs Skeptic); measures the value of query diversity | `q_12`, `q_40` |
| relevance (embedding) | Similarity score | Ranking | 0.81 |
| relevance (LLM) | Screening score 0–3 + reason | Precision filtering | 3, "same task and method" |
| screened / read / full text | Flags | Funnel counts in the coverage report | read, full text |
| used by candidates | Which candidates relied on it | The UI filter "papers for this candidate" | `cand_3` |

### 4.6 SearchQuery

**What it is:** one query sent to one source.

**Why it exists:** the coverage report must say what was searched and by whom. Failed queries must be visible, not silently ignored.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation id | Owner | — | `inv_204` |
| origin | analyst / scout / skeptic / landscape | Coverage by origin; measures the Skeptic's contribution | skeptic |
| source | Which API | Coverage per source | openalex |
| query text, filters | What was asked | Transparency; reproducibility | "hallucination small language models low-resource", year ≥ 2021 |
| result count | How many results came back | Detects dead queries | 34 |
| error | What failed, if anything | Coverage report failures; source health monitoring | "429 rate limited (after retries)" |

### 4.7 Signal (tier 3)

**What it is:** a piece of real-world information from GitHub, Hugging Face, Hacker News or the news. It suggests a practical problem or an emerging trend.

**Why it exists:** Mode 2 should find ideas that matter in practice, not just on paper. Signals are **stored separately from papers** so they can never be cited as evidence (doc 07).

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation id | Owner | — | `inv_301` |
| source | github / huggingface / hackernews / news | Display; source health | huggingface |
| URL, title | Where it is and what it's called | The user can open it | (dataset page) |
| summary | Short summary | Scout context | "Only 2 QA datasets for Georgian, both under 1k items" |
| date | When it was published or updated | Recency of the signal | 2026-08 |
| popularity metric | Stars, downloads, points | A rough importance hint | 1.2k downloads |
| topic tags | Which subtopic it relates to | Routing signals to the right part of the Scout's material | "low-resource QA" |

### 4.8 OpportunityMaterial (Mode 2)

**What it is:** the patterns that **code** mines from many PaperAnalysis records, prepared for the Scout.

**Why it exists:**

- The Scout shouldn't read 50 raw papers. It gets condensed, evidence-linked patterns instead.
- This is what makes Mode 2 "discovery from literature" instead of brainstorming. Every pattern points back to quotes.

| Type | Content | Why we need it | Example |
|---|---|---|---|
| Subtopic cluster | Label, member paper IDs, representative papers, per-year counts | The field map; shows mature vs emerging areas | "Multilingual hallucination evaluation", counts 2022: 4, 2023: 11, 2024: 29 |
| Limitation cluster | Label, member quote IDs, number of distinct papers, number of distinct author groups | Repeated limitations across independent groups are strong opportunity signals; the counts prove "recurring" | "Only large models evaluated": 5 papers, 4 groups |
| Contradiction pair | Two finding quote IDs, shared question/setting, check result | Disagreements between studies are classic research opportunities | Paper A: RAG helps small models; Paper B: no gain under 1B |
| Combination hint | Method, task, row/column counts, neighbouring examples | Method × task cells that appear empty in our sample. A *hint*, never proof | "Retrieval augmentation × low-resource QA for <1B models: 0 papers in sample" |
| Dataset hint | Dataset name, tasks, availability result | Feasibility and candidate design | "AfriQA: available, CC-BY" |

---

## 5. Investigation data: candidates and their evaluation

### 5.1 Candidate

**What it is:** a research idea under evaluation. It is either the user's own idea (Mode 1) or one created by the Scout (Mode 2, alternatives, refinements).

**Why it exists:** it is the **unifying abstraction** that lets both modes share one pipeline. Versions and parent links record how an idea evolved, so the user sees *why* the final idea looks the way it does.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| id, investigation id | Identity and owner | — | `cand_3` |
| version, parent candidate id | Refinement lineage: v2 was refined from v1 | Shows the evolution; refinement limits are counted per lineage | v2, parent `cand_1` |
| origin | user / scout discover / scout alternative / scout refine / scout replenish | Where the idea came from; the UI shows it; evaluation compares origins | scout refine |
| origin strategy | limitation / future work / contradiction / combination / practical problem / refinement | How the Scout found it. Diversity checks; the UI badge; evaluation per strategy | limitation |
| title, research question, hypothesis | The idea itself | What is evaluated and finally proposed | RQ: "Does RAG reduce hallucination of sub-1B models in Georgian QA?" |
| motivation | Why it's worth doing + inspiration links (quote IDs; signal IDs listed separately) | Grounding: ≥2 verified tier-1/2 quotes are required; signals are kept apart | Links to the "only large models evaluated" cluster |
| potential contribution (initial) | The Scout's claim of what this adds | A *hypothesis* to be judged by A6, never trusted as-is | "First evaluation of RAG for sub-1B models in Georgian" |
| complexity fit, constraint notes | The Scout's own estimate of fit | A hypothesis checked by Feasibility; also a pre-screen hint | "T1, 8 weeks, public data" |
| change note | For refinements: what changed and why | The user understands the refinement; tests check it addresses the reason | "Reduced to one language and 2 models to fit T1" |
| status | proposed / screened out / light evaluating / needs refinement / advanced / full evaluating / finalist / rejected / selected / proposal ready | The lifecycle (doc 05). Drives the board UI and the orchestrator | finalist |
| rejection reason | Triage rule + evidence | Rejections are shown openly with their reasons | "Rule 3: previously investigated (p_9001)" |
| signal-only flag | The motivation comes only from tier-3 signals | Such candidates need full novelty search before they can advance | false |

### 5.2 Facet

**What it is:** one component of a candidate's idea.

**Why it exists:** novelty is rarely all-or-nothing. A paper may share the problem but not the method. Facets make "what exactly is new" precise, and they power the prior-work matrix.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| candidate | Owner | — | `cand_3` |
| kind | problem / method / data-setting / evaluation / contribution | Matching rules treat facet kinds differently | data-setting |
| text | The facet description | What papers are matched against | "Georgian-language open-domain QA" |
| is-key flag | Whether this facet is essential to the idea's novelty | The verdict rules look at the *key* combination | true |

### 5.3 Evaluation

**What it is:** one evaluation pass of a candidate, at a given depth.

**Why it exists:**

- A candidate can be evaluated more than once: light, then upgraded to full, or re-evaluated after a refinement.
- Keeping each pass separate keeps results honest about how deep the check was. A light evaluation can never yield "Supported opportunity".

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| candidate | Owner | — | `cand_3` |
| depth | light / full | Verdict thresholds differ by depth | full |
| started / finished | Timing | Latency analysis, UI | — |
| counters | Queries, papers screened/read, Skeptic rounds | Feed the coverage report and budgets | 280 screened, 31 read, 3 rounds |
| status | running / done / partial / failed | Partial results are allowed but labelled | done |

### 5.4 FacetMatch

**What it is:** one cell of the prior-work matrix: how one paper relates to one facet of the candidate.

**Why it exists:** it's the most important input for the novelty verdict and the most inspectable part of the UI. A user can see exactly which paper covers which part of the idea, with a quote.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation, facet, paper | Which cell | — | `cand_3` × method × `p_8812` |
| overlap | same / similar / different / not addressed / unknown | The verdict engine counts "same" on key facets | same |
| quote ID | The evidence for the label | Without a verified quote, "same" doesn't count | `qt_77` |
| rationale | A short explanation | The user reads why; the Skeptic can challenge it | "Also uses BM25-based RAG" |

### 5.5 Claim and ClaimEvidence

**What it is:**

- A **claim** is a single statement that Eureka's reasoning relies on.
- **ClaimEvidence** links a claim to a quote, with a stance.

**Why it exists:**

- This is the core of the evidence ledger. Every conclusion breaks down into claims, and claims into quotes.
- Separating the claim from its evidence lets one quote support several claims, and one claim have both supporting and contradicting evidence.

**Claim fields**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation | Owner | — | — |
| kind | prior work / gap / significance / feasibility / limitation / contradiction | Verdict rules use specific kinds; the UI groups by kind | gap |
| text | The statement | What the user reads | "No retrieved study evaluates RAG for sub-1B models in Georgian QA" |
| statement type | evidence / interpretation / hypothesis | Honest labelling in the UI (doc 07) | interpretation |
| inferred flag | The agent concluded it rather than a source stating it | Gap claims are usually inferred; the UI must say so | true |
| status | **System field:** verified / unverified quote / no evidence / challenged / refuted | Only verified (or successfully defended) claims count in verdicts | challenged |
| author agent | Which agent made it | Debugging; conflict resolution rules | novelty |

**ClaimEvidence fields**

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| claim, quote | The link | — | — |
| stance | supports / contradicts / qualifies | Shows both sides; contradicting evidence feeds the Skeptic and the limitations | contradicts |

### 5.6 Challenge

**What it is:** one objection raised by the Skeptic.

**Why it exists:**

- Self-criticism must be recorded, not just "done".
- Challenges change verdicts by rule, and unresolved ones go into the report's limitations.
- The Skeptic log in the UI is built from these rows.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation, round | Where and when | Saturation tracking per round | round 2 |
| target claim | The claim being attacked | Conflict resolution needs the link | the gap claim above |
| type | prior work found / weak assumption / methodological flaw / overstated contribution / alternative explanation / feasibility risk | Different types are handled by different rules | prior work found |
| argument | The Skeptic's reasoning | The user reads it | "A 2025 workshop paper tests RAG on 0.5B models for Georgian" |
| evidence quote IDs, new paper IDs | The proof and the newly found papers | A challenge backed by a verified quote can override a novelty claim | `qt_102`, `p_9001` |
| resolution | accepted / rebutted / unresolved + link to the rebuttal claim | Shows how the system responded; unresolved challenges go into the limitations | accepted |

### 5.7 Verdict

**What it is:** the official conclusion for one dimension of a candidate.

**Why it exists:**

- It's what the user sees first.
- It is **computed by code** from structured inputs, so it is consistent and explainable (the rule trace).
- The rule-table version makes it reproducible when the rules change.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation | Owner | — | — |
| dimension | novelty / significance / feasibility / recommendation | One row per dimension | novelty |
| category | The verdict, e.g. Potential opportunity, Moderate, Feasible with adjustments, Pursue | What the UI shows | Incremental variation |
| rule trace | Rule IDs that fired + the claim IDs behind them | The "why": the user can drill down to quotes | N2 fired: claims `cl_5`, `cl_9` |
| uncertainties | What remains unknown | Required honesty (doc 07 §6) | "Closest paper available as abstract only" |
| blocking requirements | Feasibility only: what makes it impossible as-is | Drives refinement (simplify) and the user's decision | "Needs a T2 GPU for fine-tuning" |
| rule-table version | Which version of the rules produced it | Reproducibility when rules change | rules v2 |

### 5.8 CoverageReport

**What it is:** a summary of how hard Eureka looked, attached to every evaluation.

**Why it exists:** "we found nothing" means very different things after 20 papers vs 300 papers. The coverage report turns absence of evidence into an honest, qualified statement, and the verdict rules use its numbers (e.g. "Supported" requires saturation and minimum counts).

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| evaluation | Owner | — | — |
| sources and failures | Which sources were queried, which failed | Blind spots; verdict downgrades | arXiv timed out twice |
| query counts by origin | Analyst / Scout / Skeptic / landscape | Shows how wide the search was | 18 / 0 / 14 / 0 |
| date range | Years covered | Known limits ("papers after the cache date not included") | 2015–2026 |
| paper funnel | Retrieved → deduplicated → screened → read | Thresholds for verdicts; UI funnel | 620 → 410 → 280 → 31 |
| full text vs abstract | Of the papers read | Evidence depth | 9 full / 22 abstract |
| tier mix | Peer-reviewed vs preprint | Quality context | 18 / 13 |
| Skeptic saturation curve | New relevant papers per round | Required for "Supported" | 3 → 1 → 0 |
| budgets hit | Which limits stopped the work | Honest: "stopped by budget" | none |
| blind spots | Known gaps in coverage | Required in every report | "No PubMed; English only" |

### 5.9 TriageDecision

**What it is:** a record of what the orchestrator decided after an evaluation, and why.

**Why it exists:**

- Iteration (refine / reject / advance) must be explainable and auditable.
- These rows power the reasons on the candidate cards and let us debug loops.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| candidate, evaluation | What was triaged | — | `cand_1`, light |
| rule number | Which triage rule matched (doc 05 §4) | Explainability; tests | 5 |
| action | advance / refine / reject / expand search / finalize | What happened next | refine |
| evidence IDs | The verdicts/claims behind the decision | Drill-down | blocking requirement in `vd_12` |
| timestamp | When | Ordering in the UI timeline | — |

### 5.10 ResearchPackage

**What it is:** the final deliverable for a strong candidate: the 17 sections from doc 02.

**Why it exists:** this is what the student takes away. Storing it structurally (not as one blob of text) lets us tag each section's evidence status, regenerate one section at a time, and render references safely from the DB.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| candidate | Owner | — | `cand_3` |
| sections (17) | Each with content, statement-type tags and referenced claim/paper IDs | Honest labelling per section; references are IDs that the backend turns into citations | Section 13: experiment design |
| generation model, prompt version | How it was produced | Reproducibility; evaluation | `proposal v1` |
| export history | When and in which format it was exported | User convenience; auditing | md, 2026-11-20 |

---

## 6. Interaction data

### 6.1 ConversationMessage

**What it is:** one message in the clarification dialogue, a steering action, or the optional Ask panel.

**Why it exists:** conversation is part of the workflow (answers change facets; steering creates Scout tasks), so it must be stored and linked to what it caused.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation | Owner | — | — |
| role | user / eureka | Who said it | user |
| text | The message | Display; context for later answers | "Make it simpler" |
| referenced IDs | Candidates/papers/claims mentioned | Answers carry citation chips | `cand_3` |
| linked actions | What the message triggered | Traceability | "Scout refine: simplify" |

### 6.2 ProgressEvent

**What it is:** one progress update during a run.

**Why it exists:** runs take minutes. Stored events feed the live progress view (over server-sent events) and let a reconnecting browser replay what it missed.

| Field | Meaning | Why we need it | Example |
|---|---|---|---|
| investigation, time | Owner and timestamp | Ordering | — |
| stage | The current stage | The stage rail | skeptic_round_2 |
| message | Human-readable text | The activity feed | "Found 1 new relevant paper" |
| counters snapshot | Counts at this moment | Live counters, funnel chart | read 27 |
| cost so far | Spend at this moment | The live cost display | $1.40 |

---

## 7. Operational data

| Entity | What it is | Why it exists | Key fields (meaning) |
|---|---|---|---|
| **LlmCall** | One call to Claude | Cost accounting, budget enforcement, debugging, and tying evaluation results to prompt versions | investigation; agent role; model and effort (configuration used); prompt version; input / output / cache-read tokens (cost and caching effectiveness); cost; latency; API request ID (for Anthropic support); stop reason (detects refusals and truncation); error |
| **ToolCall** | One tool call made by the Scout or Skeptic | Shows how agents explore; enforces tool budgets; debugging | investigation; agent; tool; arguments; result size; latency; budget remaining; error |
| **ApiCache** | Stored responses from external APIs | Cheaper, faster, rate-limit-safe and repeatable runs (demo safety) | source; normalized request key; response; fetched at; time-to-live |

---

## 8. Important constraints and indexes

| Rule | Why |
|---|---|
| Paper external IDs are unique where present | Dedup: the same paper from two sources becomes one record |
| PaperAnalysis is unique per (paper, extractor version) | The cache works, and old and new prompt formats never mix |
| Vector indexes on paper and chunk embeddings; keyword index on chunks | Fast semantic + exact-term (hybrid) search |
| Candidates keep their full lineage; nothing is deleted on refinement | The user (and evaluation) can see how ideas evolved and why |
| The constraints snapshot and config version are stored on the investigation | Every result stays reproducible and explainable later |
| Quotes can only reference existing papers/chunks | Structural protection against invented sources |
| Signals can never be linked through ClaimEvidence | Tier-3 data can't become evidence (enforced by schema + verifier) |
| Deleting an investigation cascades to its own rows, never to global papers/analyses | User privacy without destroying shared knowledge |

---

## 9. From agent output to database rows (the bridge to doc 08)

An agent's output schema is **not** the same as a database table. The LLM fills only the "judgment" fields; code adds identity, links and trust fields. This table shows the split for every agent.

| Agent → output shape | What the LLM fills | What code adds afterwards | Stored as |
|---|---|---|---|
| A1 → StructuredIdea | Title, question, facets (kind, text, is-key), assumptions, vagueness flags, questions | IDs, link to the investigation, user answers | StructuredIdea + Candidate v1 (origin user) + Facets |
| A1 → FieldBrief | Subtopics + reasons, query plan, signal queries, questions | IDs, user edits | FieldBrief + SearchQuery rows when executed |
| A1 → QueryPlan | Queries with their type (synonym, facet-only, method-free, …) | **Quota check** (regenerate if missing), execution, result counts | SearchQuery |
| A2 → CandidateSet | Per candidate: title, question, hypothesis, origin strategy, motivation, inspiration quotes (paper ID + text), contribution, facets, fit notes, change note | IDs, version/parent, origin, **quote verification, grounding check (≥2 verified tier-1/2), dedup, hard-constraint filter**, status | Candidate + Facet + Quote + inspiration links |
| A3 → PaperAnalysis | All extracted fields, with quotes (text + chunk ID) | Paper ID, extractor version, model, **source level, quote verification** | PaperAnalysis + Quote |
| A4 → NoveltyAssessment | Facet matches (label, quote, rationale), closest works, claims (text, kind, statement type, inferred, quotes with stance), proposed category, uncertainties, challenge responses | **Quote verification, claim status, verdict category (the proposed one is only advisory)** | FacetMatch, Claim, ClaimEvidence, Quote; the Verdict is computed by code |
| A5 → ChallengeSet | Challenges (target, type, argument, quotes, new paper IDs), saturation count | **Paper ID existence check, quote verification**, round number, resolution after A4 responds | Challenge (+ new InvestigationPaper rows) |
| A6 → QuickFeasibility | Level, blocking requirements, simplifications | **Hard-constraint check** (code can override to "not feasible") | Verdict (feasibility, preliminary) |
| A6 → Significance + Feasibility assessments | Rubric answers with claims and quotes, beneficiaries, timeline, datasets, risks, simplifications | **Dataset lookup results attached, quote verification, verdict categories by rule** | Claim, ClaimEvidence, Verdict |
| A7 → ResearchPackage | Section contents with statement-type tags and referenced IDs | **Reference rendering from DB records, a check that every ID exists**, versioning | ResearchPackage |

**Practical consequence for prompt writing:** each agent's prompt explains its output shape field by field, using the "Meaning" and "Why we need it" columns in this document. A model that understands *why* a field exists fills it better.

---

## 10. Walk-through: what gets written during one Mode 1 run

Using the running example:

1. **The user submits the idea** → `Investigation` (mode evaluate, beginner, constraints snapshot), with status *created*.
2. **A1 runs** → `StructuredIdea`, `Candidate` v1 (origin user), 5 `Facet` rows. Status *clarifying*. The user answers "Georgian and Armenian" → stored; status *running*.
3. **A1 query plan** → 18 `SearchQuery` rows → retrieval creates/updates `Paper` rows (global) and `InvestigationPaper` links (relevance, flags).
4. **Full text** for 9 open-access papers → `Chunk` rows.
5. **A3 reads 31 papers.** Most `PaperAnalysis` rows are new; 6 were already cached from an earlier investigation and reused at no cost. Their quotes are verified → `Quote` rows.
6. **`Evaluation` (full) starts.** A4 → 5 × 25 `FacetMatch` cells, 12 `Claim` rows with `ClaimEvidence`.
7. **Skeptic round 1** → 4 `Challenge` rows, 14 more `SearchQuery` rows (origin skeptic), 3 new papers. A4 accepts one challenge (a 2025 paper is "same" on method and setting).
8. **Rounds 2–3** → saturation 3 → 1 → 0. `CoverageReport` completed.
9. **A6** → significance and feasibility claims. The **Verdict engine** writes `Verdict` rows: novelty *Incremental variation*, significance *Moderate*, feasibility *Feasible with adjustments*, recommendation *Refine*.
10. **Triage** → `TriageDecision` (rule 4, refine). Scout → `Candidate` v2 (parent v1, change note "focus on sub-1B models and Armenian, where the 2025 paper didn't test"). The light re-check → new `Evaluation`, then `Verdict` rows (novelty *Potential opportunity*, recommendation *Pursue*).
11. **The user requests a proposal** → `ResearchPackage`.

Throughout the run: `ProgressEvent` rows stream to the UI; every model call writes an `LlmCall` and every tool call a `ToolCall`; repeated API requests hit `ApiCache`.

**In Mode 2** the same entities are used, plus a `FieldBrief` at the start, `Signal` and `OpportunityMaterial` rows before generation, and many `Candidate` rows (8–12) with `TriageDecision` rows recording which were rejected, refined or advanced.

---

## 11. Agent output shapes (summary)

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
