# 07: Evidence and Verdicts

Reliability is Eureka's core product feature. This document defines:

- how evidence is recorded and verified,
- how verdicts are decided,
- how uncertainty is communicated.

## 1. Evidence ledger

Every conclusion Eureka shows can be traced like this:

```text
Verdict ──► Claims ──► Quotes ──► Passage (chunk or abstract) ──► Paper record (DOI / arXiv / S2 link)
```

### 1.1 Types of statements

| Type | Meaning | How it's shown |
|---|---|---|
| **Evidence** | Backed by ≥1 verified quote from a tier-1/2 source | Normal text with citation chips |
| **Interpretation** | An agent's reasoning from evidence (e.g. "this suggests the method hasn't been tested on X") | Marked "Eureka's interpretation", linked to the evidence it reasons from |
| **Hypothesis** | An untested idea (e.g. a proposed hypothesis, an expected outcome) | Marked "hypothesis" |
| **Signal** | Tier-3 community/industry information | Marked "signal (not scientific evidence)"; never used in verdicts |

### 1.2 Source levels

Every quote records where it came from:

- **full text:** from a parsed open-access PDF chunk.
- **abstract:** from the abstract only. The UI says "based on abstract".
- **metadata:** title, venue, year, citation count.
- **model inference:** not a quote; only allowed inside interpretations.

### 1.3 Source tiers

| Tier | Sources | Can support verdicts? |
|---|---|---|
| 1 | Peer-reviewed publications | Yes |
| 2 | Preprints (arXiv etc.) | Yes, with a "preprint" badge; the tier mix is reported |
| 3 | GitHub, Hugging Face, forums, news | **No**, motivation only |

## 2. Anti-fabrication guarantees (enforced by C4, the Evidence Verifier)

| # | Guarantee | Mechanism |
|---|---|---|
| 1 | No invented papers | Agents reference only internal paper IDs from their context packet. Unknown IDs → the reference is dropped, the claim is downgraded, and a hallucination counter is incremented |
| 2 | No invented quotes | Every quote is substring-matched against the stored chunk/abstract after normalizing whitespace, Unicode, hyphenation and quote marks, with a fuzzy-match threshold of about 0.95. Failure → status *unverified quote*, excluded from verdicts, warning shown |
| 3 | No invented metadata | References in reports are rendered by the backend from DB records; titles, authors and DOIs are never written by an LLM. DOIs are checked against Crossref/OpenAlex |
| 4 | No signals as evidence | The verifier rejects tier-3 items in supporting/contradicting evidence lists |
| 5 | No pretending to have read full text | The source level is stored per quote; any agent statement implying full-paper analysis of an abstract-only paper is flagged |
| 6 | Optional stronger mode | For key evidence steps, Claude's **Citations** feature (document blocks with citations enabled) returns exact cited spans. It can't be combined with structured outputs in the same call, so it runs as a separate extraction step. A post-MVP option |

## 3. Claims

A claim is a single statement used in reasoning. Its fields are listed in doc 10.

- **Kinds:** prior work exists, gap, significance, feasibility, limitation, contradiction.
- **Status:** verified / unverified quote / no evidence / challenged / refuted.
- **Stance links:** each quote supports, contradicts or qualifies the claim.

Verdict rules only count claims with status **verified**. Challenged claims count only if the challenge was rebutted with evidence.

## 4. Verdict rules

The LLM agents produce *structured assessments*: facet matrices, rubric answers and claims. The **Verdict Engine** (code) turns those into categories. Rules are versioned tables, so a change in a rule is a reviewed change, not a prompt tweak.

### 4.1 Novelty

| Category | Rule (all conditions must hold) | User-facing wording |
|---|---|---|
| **Previously investigated** | ≥1 paper is "same" on the key facets (problem **and** method **and** data/setting), with a verified quote | "This has been studied before. See [closest work]." |
| **Incremental variation** | The closest work differs in only one minor facet (same kind of data, same method family), and the Skeptic found no meaningful difference | "Very close to existing work. The difference may be too small for a contribution." |
| **Potential opportunity** | No "same" match on the key combination, **but** at least one weakness: coverage below threshold, Skeptic stopped by budget before saturation, key evidence abstract-only, or the gap is mostly inferred | "Possibly unexplored. We found no matching work, but our search has limits: [list]." |
| **Supported opportunity** | No "same" match on the key combination; coverage ≥ thresholds (default: ≥150 screened, ≥25 read, ≥3 sources); the Skeptic loop reached **saturation**; every gap claim verified | "No matching work found under thorough search (N papers, sources, date). This is **not a guarantee of originality**." |
| **Insufficient evidence** | Retrieval failed or was too thin (e.g. <20 relevant papers, source outages) | "We couldn't gather enough evidence to judge." |

**Words Eureka never uses** for a novelty result: "original", "novel" (as a verdict), "nobody has studied", "verified gap", "guaranteed".

Light evaluations use lower coverage thresholds. They can only produce **Potential** at best, never **Supported**. Supported requires full evaluation.

### 4.2 Significance

| Level | Criteria |
|---|---|
| **Strong** | The problem is identified as open or important in ≥2 independent papers or a survey (verified quotes), **and** the candidate's contribution would close a substantial part of the gap relative to the closest works |
| **Moderate** | The problem is documented as relevant (≥1 paper or a clear practical need), and the contribution is meaningful but partial |
| **Weak** | Little evidence the problem matters, or the contribution over the closest works is marginal |
| **Unclear** | Not enough evidence either way |

Each level is shown with its supporting claims and "who benefits".

### 4.3 Feasibility

| Level | Criteria |
|---|---|
| **Feasible** | All hard constraints are met; the required data is confirmed available; the needed skills are in the profile or learnable within the time; the timeline fits |
| **Feasible with adjustments** | Feasible if specific listed changes are made (smaller model, subset of the data, narrower scope) |
| **Not feasible under your constraints** | A blocking requirement can't be met (compute tier, data access, ethics, time) |
| **Unclear** | Key information is missing (e.g. dataset availability unknown) |

The **blocking requirements** list always overrides the level.

### 4.4 Final recommendation

| Recommendation | Rule |
|---|---|
| **Pursue** | Novelty ∈ {Supported, Potential with coverage ≥ thresholds}; significance ∈ {Strong, Moderate}; feasibility ∈ {Feasible, Feasible with adjustments} |
| **Refine** | Exactly one dimension falls short and a concrete refinement exists (from A6's simplifications or the open facets in A4's matrix) |
| **Reconsider** | Novelty = Previously investigated; or feasibility = Not feasible with no viable simplification; or significance = Weak |
| *(flag)* Needs further investigation | Novelty = Insufficient evidence, or Potential with low coverage; shown alongside the recommendation |

Each recommendation shows its **rule trace**: which rules fired, and the claim IDs behind them.

### 4.5 Conflict resolution between agents

| Conflict | Resolution |
|---|---|
| Skeptic challenge vs Novelty Investigator claim | A challenge with a verified quote showing "same" on the key facets wins. A challenge without evidence is shown but doesn't change the verdict |
| Novelty Investigator rebuts a challenge | The rebuttal counts only if it cites evidence (e.g. a quote showing the paper's setting differs) |
| Value Evaluator says strong, Skeptic says overstated | The significance level drops one step unless the Value Evaluator's supporting claims include ≥2 verified quotes that the challenge doesn't address |
| Scout's fit notes vs Feasibility verdict | The Feasibility verdict always wins. The Scout's notes are hypotheses |

## 5. Coverage report (attached to every verdict)

- Sources queried, and each source's failures.
- Number of queries (by origin: analyst / Scout / Skeptic).
- Date range searched.
- Papers: retrieved → deduplicated → screened → read, and of those read, full text vs abstract only.
- Tier mix (peer-reviewed vs preprint).
- Skeptic rounds and new relevant papers per round (the saturation curve).
- Budget limits that were hit.
- **Known blind spots**, e.g. "PubMed not searched", "non-English literature not covered", "papers after <cache date> not included".

## 6. Communicating uncertainty

- **No percentages.** Categories + rule traces + coverage.
- Every verdict card shows: the category, a one-sentence reason, the top 2–3 evidence chips, and an "Uncertainties" list.
- The "remaining uncertainties" from A4 and the unresolved Skeptic challenges always appear in the report and in the research package's limitations section.
- When evidence is abstract-only for the closest works, the verdict card says so explicitly.

## 7. Evidence graph

The nodes are database rows:

- Candidate
- Facet
- Paper
- Claim
- Method
- Dataset
- Limitation cluster
- Contradiction
- Signal (visually distinct)

The edges come from foreign keys:

- supports, contradicts, qualifies
- covers facet
- uses method, uses dataset
- inspired by (candidate → limitation/contradiction/signal)
- refined from (candidate → candidate)
- cites (paper → paper)

It is rendered in the frontend (doc 12). No graph database is needed.
