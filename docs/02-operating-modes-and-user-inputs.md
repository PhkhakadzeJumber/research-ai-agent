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
