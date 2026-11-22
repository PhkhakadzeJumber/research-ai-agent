# 02: Operating Modes and User Inputs

## 1. The two modes at a glance

| | Mode 1: Evaluate My Research Idea | Mode 2: Find Me a Research Idea |
|---|---|---|
| Starts with | The user's idea | A broad field + interests |
| First agent | Idea Analyst (structures the idea) | Idea Analyst (field brief), then the Research Opportunity Scout |
| Number of candidates | 1 (the user's idea), plus alternatives if it fails | 8–12 generated → ~6 light-evaluated → ≤3 fully evaluated |
| Typical result | A verdict on the idea + refinements/alternatives + a proposal | Ranked opportunities with research packages + a list of rejected candidates with reasons |
| Shared | Search layer, Paper Reader, Novelty Investigator, Skeptic, Value & Feasibility Evaluator, Proposal Designer, evidence ledger, verdict rules, UI workspace |

**Unifying concept: everything Eureka evaluates is a *candidate*.** In Mode 1 the user's idea becomes candidate v1 (origin: user). In Mode 2 the Scout creates candidates. The evaluation pipeline doesn't know or care where a candidate came from. That is what keeps the two modes from becoming two systems.

A Mode 2 candidate can be promoted to a **deep dive**: a full Mode 1 investigation on that candidate.

## 2. Inputs

### 2.1 Mode 1 inputs

| Field | Required | Notes |
|---|---|---|
| Idea text | Yes | Free text: a question, hypothesis, topic or short abstract (up to ~2,000 characters) |
| Field / subfield | Optional | Helps query planning; inferred if missing |
| Known related papers | Optional | DOIs, arXiv IDs or titles the user already knows. Used as seeds and never counted as Eureka's own discoveries |
| Research document | Optional (§2.4; `.md`/`.txt` since step 6a, `.pdf` since 6b) | The user's own draft, proposal or paper (text or Markdown first, then PDF). Read in full and used instead of the short idea text; treated by the rules in §2.4 |
| Complexity level | Yes (default: Intermediate) | See section 3 |
| Constraints profile | Yes (with defaults) | See section 4 |

### 2.2 Mode 2 inputs

| Field | Required | Notes |
|---|---|---|
| Field | Yes | Picked from a list (AI, ML, Robotics, Computer Vision, Autonomous Systems, NLP, AI Agents, Data Science, …) or free text |
| Interests / subtopics | Optional | Free text, e.g. "evaluation of LLM agents, low-resource languages" |
| Preferred technologies | Optional | e.g. "PyTorch, small language models, ROS" |
| Exclusions | Optional | Topics or methods to avoid |
| Application domain | Optional | e.g. healthcare, education, agriculture |
| Complexity level | Yes | See section 3 |
| Constraints profile | Yes (with defaults) | See section 4 |
| Number of opportunities wanted | Optional (default 3) | Upper bound on finalists |

### 2.3 Clarification step (both modes)

After intake, the Idea Analyst shows its structured reading of the request:

- **Mode 1:** the idea's facets.
- **Mode 2:** a *field brief*: the subtopics it plans to explore and the search directions.

Along with that, it asks **at most 3 clarifying questions**. The user can answer, edit the facets/brief, or skip. When the user skips, Eureka proceeds with its stated assumptions and lists them in the report. This is the main human-in-the-loop checkpoint.

**One exception to skipping:** if the Idea Analyst had to *propose* a key facet (the user's text didn't state it, e.g. no method for "make LLMs better at low-resource languages"), the user must confirm or edit the reading explicitly before anything is evaluated. Otherwise Eureka could end up judging an idea the user never had.

### 2.4 The user's research document (Mode 1; built in steps 6a-6b: text, Markdown and PDF)

A real research idea rarely fits in 2,000 characters. The user may upload their own draft, proposal or paper, and Eureka evaluates that. No new agent is needed: the existing agents get a new input or task.

**How it flows through the pipeline:**

1. **Upload (code).** Text and Markdown first; PDF once a parser library is approved (a new dependency); DOCX later. Limits are about 10 MB and 50 pages. At upload the user agrees that the document is sent to Claude for reading, and answers "Is this already published or on arXiv?" (with an optional ID).
2. **Reading (A3 Paper Reader).** The document is read like a paper: the same schema, with quotes verified against the user's own text. That gives research questions, method, data, claimed contributions, limitations, and the papers it cites.
3. **Structuring (A1 Idea Analyst, a new task).** A1 builds the facets from that reading. Each facet points to the passage of the document it came from.
4. **The cited papers become seeds.** They are searched for and read like any known related paper.
5. **The evaluation** runs as usual. **A6** also gets the document's method, data and plan sections, so feasibility judges what the user actually planned. *(As built: A4 doesn't get excerpts; it compares the idea's facets, which already come from the document, with the papers.)* **A5 Skeptic** gets the document's novelty claims as targets ("no one has done X" is a claim to refute, not a fact).
6. **The report** relates the two sides: "your document claims X (section 2); the literature shows Y (paper Z)".

**Three rules:**

- **The document is never evidence for itself.** Its quotes are verified, so Eureka can show *where* the user said something. But they are labelled "your document", and verdicts count only the published literature (tier 1/2).
- **The user's own work never counts against them.** If the search finds the document itself (by the given ID, or a matching title and author list), that paper is marked **"your own work"**. It is kept out of the prior-work matrix and the verdicts, and shown separately in the report. The papers the document *cites* are the opposite case: they are prior work, and if one of them already did the idea, Eureka says so.
- **The document stays private.** It is stored only with its investigation and deleted with it. It is never added to the shared paper library (doc 10 §5) and never sent to the search APIs; only the short keyword queries built from its facets are. It goes to Claude only for reading and evaluation, with the user's consent. Its text is data, never instructions, like any paper text (doc 04).

*As built (step 6a.1):* `python -m scripts.evaluate --idea-file draft.md [--consent] [--published --doi/--arxiv ...]` reads `.md` and `.txt` up to 60,000 characters (about 15–20 pages). The file is split into sections D1, D2, … by its headings (by paragraphs for plain text), and the command asks for consent before anything is sent; with `--yes`, `--consent` is required. The Paper Reader reads the sections with quote verification, and the reading is stored on the `user_documents` row (`src/ai/workflows/user_document.py`). The Idea Analyst's **document task** (prompt `idea_analyst_document`, output `DocumentIdeaOutput`) builds the usual reading, which is stored as the Intake, so clarification is unchanged. Each facet's section and the document's novelty claims are stored with the document (`idea_extras`), and the command shows them before clarification. *As built (step 6a.2):*
- **Your own work** (`src/ai/workflows/own_work.py`) is set aside before any screening, both the first and every Skeptic round's. A hit counts as your own work if its DOI or arXiv ID matches the one you gave (arXiv versions ignored), or if you said the document is published and the paper's title closely matches the document's or the idea's title. It is marked screened with score 0 and the reason "your own work", so it is never read, never in the prior-work matrix and never in a verdict, and the report lists it.
- The document's **novelty claims** reach the Skeptic (`<document_claims>`; prompts `skeptic_queries` v3 and `skeptic_challenges` v2): it plans a search against each one, and a new paper that contradicts one becomes a prior-work or overstated-contribution challenge.
- The document's **method, data and plan sections** reach Value & Feasibility (`<user_plan>`; prompt `value_feasibility` v2) as the user's statement, never as evidence.
- The Mode 1 report adds **"Your document"** (file, sections, quote checks, where each facet came from) and **"Your document's claims vs the literature"** (the claims, the novelty verdict, the relevant Skeptic challenges, and the papers set aside as your own work).

*As built (step 6b):* `--idea-file draft.pdf` works too, with up to 50 pages and the same 60,000 characters. The text is extracted page by page with **pypdf** (BSD license; the one dependency this feature added). Page-number lines are dropped, words broken across lines are joined, and headings are detected (numbered like "2. Related work", common section names, short all-capitals lines). The reference list is dropped, and the text is then split into sections like Markdown. A scanned PDF without text, a password-protected PDF or a damaged file is refused with a clear message. DOCX could come later the same way.

Cost: reading the document is about 5–15 cents (Opus, depending on length), and the excerpts add a few cents per agent. A full evaluation stays about $3–5.

*Mode 2:* no upload for now. Inferring interests from the user's past papers is a possible later addition, but the text inputs already cover it.

## 3. Complexity levels

Complexity is the user's *desired ambition*. Constraints are the user's *actual limits*. Both influence generation (Scout), evaluation (Feasibility) and the proposal (methodology, experiment design).

| Dimension | Beginner / Easy | Intermediate | Advanced / Complex |
|---|---|---|---|
| Typical research question | A well-scoped empirical question in an underexplored setting | A comparison or improvement question needing independent design choices | A new method, a new problem formulation, or a substantial system |
| Typical contribution type | Replication + extension in a new setting; systematic evaluation; careful negative result; new small benchmark/dataset slice | Comparative study with ablations; adapting a method to a new domain; a modest method improvement | A novel method or architecture, a theoretical insight, a large-scale empirical finding |
| Methods | Existing models/libraries used as-is; standard metrics | Implementation and modification of existing methods; multiple baselines | Custom methods, heavy engineering, advanced training regimes |
| Data | Existing public datasets | Public datasets, possibly with preprocessing or light annotation | May require new data collection, large datasets or simulation |
| Compute tier (default ceiling) | T0–T1 | up to T2 | up to T3 |
| Typical duration | 1–3 months | 3–6 months | 6+ months |
| Expected outcome | A solid course or bachelor's paper, workshop-level | A master's thesis or workshop/conference paper | A conference or journal paper |

**Compute tiers:**

| Tier | Meaning |
|---|---|
| T0 | Laptop CPU |
| T1 | Free or cheap cloud notebook GPU (limited hours) |
| T2 | One modern GPU for days |
| T3 | Multi-GPU or cluster for days or weeks |

### 3.1 "Easy" must still be meaningful

A Beginner candidate must pass the same evaluation as any other. It must also meet a **meaningful-minimum** standard:

1. **The answer isn't already known.** The novelty verdict is at least *Potential opportunity* (doc 07). A pure re-run of an existing study with nothing new is rejected.
2. **The result is useful to someone.** It informs practitioners, extends a finding to a new setting/language/population, compares methods fairly where no such comparison exists, or tests a claim that hasn't been tested.
3. **The design is valid.** It has a clear question, baselines and metrics, so the result can actually be interpreted.

Examples of good Beginner shapes: "Does method X's reported advantage hold on low-resource language Y?", "A systematic comparison of A, B and C under condition Z that the original papers didn't test", "Replicating study S on a public dataset and probing its stated limitation L".

### 3.2 Conflicts between complexity and constraints

If the requested complexity doesn't fit the constraints (e.g. Advanced + 1 month + laptop only), Eureka tells the user at intake and offers two options:

- Keep the constraints and lower the effective complexity.
- Keep the complexity and mark candidates as "needs more resources than you listed".

**Constraints always win in the feasibility verdict.**

## 4. Constraints profile

Saved per user so it doesn't have to be re-entered. Can be edited per investigation.

| Field | Values / examples | Used by |
|---|---|---|
| Academic level | Undergraduate / Master's / PhD / Researcher | Scout, Feasibility, Proposal |
| Available time | Weeks or months; hours per week | Feasibility (timeline estimate), Proposal (schedule) |
| Technical skills | Checklist + free text (Python, PyTorch, statistics, ROS, web scraping, …) | Scout (method choice), Feasibility (skill gaps) |
| Background knowledge | Free text (courses taken, prior projects) | Scout, Feasibility |
| Compute | Tier T0–T3 + free text (e.g. "free notebook GPU only") | Feasibility (blocking check), Scout |
| Budget for data/APIs | None / small / some | Feasibility |
| Data access | Public only / can collect / institutional access | Scout, Feasibility |
| Preferred technologies / subfields | Free text | Scout, query planning |
| Ethics constraints | e.g. "no human-subject data" | Scout, Feasibility (blocking) |
| Supervision available | Yes / limited / no | Feasibility (risk level) |
| Other limitations | Free text | Scout, Feasibility |

**Hard vs soft constraints.**

- *Hard:* compute ceiling, time limit, ethics, data access. Code filters on these before and after LLM judgment.
- *Soft:* preferences and skills. The LLM weighs these.

## 5. The research package (what Eureka delivers per strong candidate)

Each section shows its producer and its evidence status. In the UI, every section shows whether its content is **retrieved evidence**, **agent interpretation**, or **hypothesis**.

| # | Section | Produced by | Evidence status |
|---|---|---|---|
| 1 | Proposed title | Proposal Designer | Interpretation |
| 2 | Research problem and background | Proposal Designer, from claims in the ledger | Cited evidence |
| 3 | Research question | Scout / Idea Analyst, refined in evaluation | Interpretation |
| 4 | Motivation | Scout (inspiration evidence) + Value Evaluator | Cited evidence + interpretation |
| 5 | Potential contribution | Value Evaluator | Interpretation, bounded by the novelty verdict |
| 6 | Novelty assessment | Novelty Investigator + verdict rules + Skeptic log | Verdict + coverage report |
| 7 | Closest related papers and differences | Novelty Investigator (facet matrix) | Cited evidence |
| 8 | Supporting and contradicting evidence | Evidence ledger | Cited evidence |
| 9 | Methodology | Proposal Designer | Interpretation, grounded in methods from cited papers |
| 10 | Hypothesis (where appropriate) | Proposal Designer | Hypothesis, labelled as such |
| 11 | Datasets and availability | Feasibility Evaluator + dataset lookup tool | Checked links / metadata |
| 12 | Tools and technologies | Proposal Designer | Interpretation |
| 13 | Experiment design (baselines, comparisons, metrics) | Proposal Designer | Grounded in baselines/metrics from cited papers |
| 14 | Feasibility (complexity, time, resources) | Feasibility Evaluator | Rubric verdict + blocking requirements |
| 15 | Limitations and risks | Skeptic + Feasibility + Proposal Designer | Mixed, labelled |
| 16 | References | Rendered from DB records only | Verified metadata (DOI/arXiv/S2 links) |
| 17 | Final recommendation: Pursue / Refine / Reconsider | Verdict engine (rules) + one-paragraph explanation | Rule trace shown |

**Recommendation rules (summary; full rules in doc 07):**

- **Pursue:** novelty is *Supported opportunity*, or *Potential opportunity* with adequate coverage; significance is at least moderate; feasibility is *Feasible* or *Feasible with adjustments* under the user's constraints.
- **Refine:** exactly one dimension falls short in a way that a concrete change can fix (narrow the scope, swap the dataset, add a missing facet).
- **Reconsider:** previously investigated, or not feasible with no viable refinement, or weak significance.
