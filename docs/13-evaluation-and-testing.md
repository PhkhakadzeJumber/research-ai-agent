# 13: Evaluation and Testing

We must *show* that Eureka is more useful than simpler approaches, and report honestly where it isn't. This document covers the **evaluation** (does it work better?) and the **testing** (does it work correctly?).

## 1. Systems compared

| System | Description |
|---|---|
| **B1: Claude alone** | Same model, no tools. Mode 1 prompt: "Assess the novelty, significance and feasibility of this idea; cite related work." Mode 2 prompt: "Suggest research ideas in field X for this profile, with references." |
| **B2: Claude + basic RAG** | One search query → top-10 Semantic Scholar abstracts → one prompt. (Mode 2: field query → top-20 recent abstracts → "suggest ideas".) |
| **Eureka** | Full system |
| **Ablations** | Eureka without the Skeptic; without query diversification; without opportunity mining (the Scout gets raw abstracts only); light evaluation only |

The ablations show *which components matter*. That is stronger evidence than comparing whole systems alone.

## 2. Evaluation dataset

Small and carefully built: about 25–35 cases in total, in the demo domain (AI/ML/NLP).

### 2.1 Mode 1 cases

| Set | How it's built | Ground truth |
|---|---|---|
| **Known prior work (~12)** | Take recent real papers. Rewrite each paper's core idea as a "user idea" with different terminology. Working solo, avoid copying the paper's wording: write the rewrite from a short summary a few days after reading the paper, without looking at the abstract, and check that the key terms differ. A classmate or supervisor can write some of them, if available | The source paper + 2–5 human-labelled related papers. The expected verdict is "previously investigated" |
| **Date-cutoff twin (same ~12)** | Run the same cases with search restricted to before the source paper's date | Should *not* be "previously investigated" unless real earlier work exists. Tests both directions |
| **Vague ideas (~4)** | Deliberately underspecified ideas | Expected vagueness flags and sensible questions |
| **Infeasible for profile (~4)** | E.g. "pretrain a large model from scratch" + undergraduate + T0 | Expected "not feasible" + useful simplifications |
| **Hard retrieval (~3)** | Niche terminology or older literature | Known related papers |

### 2.2 Mode 2 cases

| Set | How it's built | What it measures |
|---|---|---|
| **Field × profile runs (~6)** | e.g. NLP × undergraduate/3 months/T1; AI Agents × master's/6 months/T2; Computer Vision × beginner | Human rubric on finalists; constraint compliance |
| **Planted candidates** | Inject 2 known, already-published ideas into the candidate pool in each run | Does the pipeline reject them as "previously investigated"? (rejection correctness) |
| **Time-travel discovery (stretch)** | Run Mode 2 with the literature restricted to before date D (e.g. 2 years ago). Check whether surviving candidates resemble papers published *after* D | Evidence that discovered opportunities are real, not just plausible |

## 3. Metrics

### 3.1 Mode 1

| Metric | How | Automated? |
|---|---|---|
| Known-paper recall@k | Is the source paper among the top-k closest works? | Yes |
| Related-paper recall / precision | Against the labelled set | Yes |
| Novelty verdict accuracy | Against expected verdicts, in both cutoff conditions | Yes |
| Skeptic rescue rate | Of cases where the first pass missed the source paper, the share where the Skeptic found it | Yes |
| Feasibility accuracy | Against expected verdicts on profile cases | Yes |
| Vagueness detection | Expected flags present | Rubric (semi-automatic) |

### 3.2 Mode 2

| Metric | How | Automated? |
|---|---|---|
| Grounding rate | Share of candidates whose inspiration quotes verify **and** actually state the limitation/gap claimed | Partly (verification automatic; "actually states" by sampled human check) |
| Planted rejection rate | Share of planted known ideas rejected as previously investigated or incremental | Yes |
| Constraint compliance | Share of finalists whose requirements respect hard constraints | Yes (from feasibility fields) + spot check |
| Finalist quality | Blinded human rubric (1–5): specificity, grounding, significance, feasibility, novelty plausibility. Rated **blind**: all systems' outputs shuffled and anonymized before scoring. You are the main rater; ask a supervisor or classmate to rate a subset if possible and report the agreement. Otherwise report the single-rater limitation | Human |
| Diversity | Number of distinct origin strategies and subtopics among finalists | Yes |
| Comparison vs B1/B2 | The same rubric applied blind to B1/B2 suggestions | Human |
| Time-travel hit rate (stretch) | Share of finalists matching later-published work | Human-assisted |

### 3.3 Reliability (both modes)

| Metric | Target |
|---|---|
| Fabricated references (unknown IDs / unresolvable DOIs) in final outputs | **0** |
| Quote verification rate | ≥95% of quotes verify; failures excluded from verdicts |
| Claim support rate | Share of evidence-type claims with ≥1 verified quote |
| Signals used as evidence | **0** |
| Consistency | 3 runs per case: verdict agreement and overlap of the closest-works sets |
| Cost and latency | Per mode, per stage, from the call tables |

### 3.4 Reporting

- Per-case result tables plus summary numbers.
- With ~30 cases, results are **indicative**, not statistically conclusive, and we say so.
- Failures are analysed and listed. Where B1/B2 match or beat Eureka on a metric, the report says that too.
- An evaluation dashboard page in the app (optional) shows the latest run.

**LLM-as-judge:** allowed only as a secondary signal (e.g. pre-sorting rubric candidates). It never replaces the human rubric for headline claims.

## 4. Testing strategy

### 4.1 Unit tests (fast, no network)

- Dedup (exact and fuzzy cases, including tricky near-duplicates).
- Ranking/score function.
- **Verdict rules:** table-driven tests, from facet matrices + coverage to the expected category.
- **Triage rules:** from verdict combinations to the expected action.
- Quote verifier (Unicode, whitespace, hyphenation, line breaks, near-misses).
- Budget guards and stopping conditions.
- Opportunity miners on fixtures: limitation clustering thresholds, contradiction detection, matrix gaps.
- Hard-constraint filter.
- Source adapter parsers, using recorded API responses.

### 4.2 Agent contract tests

For each LLM role, with fixed fixture inputs:

- The output validates against the schema.
- Only provided IDs are referenced.
- Quotes verify.
- Role-specific rules hold (A1 query quotas; Scout grounding + diversity + constraint compliance; Skeptic labels challenges without evidence correctly; A7 includes all 17 sections).

These run against **recorded responses** in continuous integration (cheap, deterministic), and against the **live model** in a manual or nightly job.

### 4.3 Failure-injection tests (mocked HTTP)

- A storm of Semantic Scholar rate-limit errors; OpenAlex server errors; arXiv timeouts.
- Corrupt or missing PDFs.
- An empty search result for a whole subtopic.
- An LLM returns an unknown paper ID / a paraphrased "quote" / invalid JSON / a refusal / output cut off at max tokens.
- Conflicting evidence (two papers with opposite findings).
- A signal used as evidence (must be rejected).
- Budget exhaustion in the middle of the Skeptic loop and in the middle of Mode 2 light evaluations.

### 4.4 Integration tests

- **Workflow A** end to end on a fixture corpus (no network): the report has all sections, the verifier passes, triage runs, costs are recorded.
- **Workflow B** end to end on a fixture corpus: candidates generated → pre-screen → light evaluation → triage (including one refine and one reject) → finalists → package.
- **Resume test:** kill the process mid-run, restart, and check the investigation completes.

### 4.5 End-to-end smoke tests (live, manual)

Two Mode 1 ideas and one Mode 2 field before every demo, with a pre-warmed cache.

## 5. When tests and evaluations run

| What | When |
|---|---|
| Unit + contract (recorded) + integration | Every push (CI) |
| Live contract tests | Nightly/manual, with a small budget |
| Full evaluation | At the end of weeks 2, 3 and 4, and after any prompt or rule change that affects verdicts |
