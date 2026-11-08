# 05: Workflows and Orchestration

The orchestrator (C1) is a **deterministic state machine** written in code. LLMs never decide the control flow. They return structured assessments, and code rules decide what happens next. That makes runs reproducible, testable and resumable after a crash.

## 1. Shared building block: candidate evaluation

Both workflows call the same evaluation routine on a candidate, at one of two depths.

| Step | Light evaluation (screening) | Full evaluation (finalists, Mode 1 idea) |
|---|---|---|
| 1. Normalize candidate (A1) | Yes | Yes |
| 2. Query plan (A1) | 8–10 queries | 15–20 queries |
| 3. Targeted retrieval (C2) | ~100 screened | ~300 screened + citation snowballing |
| 4. Read (A3) | Top ~12 (cached analyses reused) | Top ~30–40 + full text for the top ~10 open-access papers |
| 5. Novelty Investigator (A4) | Yes | Yes |
| 6. Skeptic loop (A5 ⇄ A4) | 1 round | Up to 3 rounds, stop at saturation |
| 7. Value & Feasibility (A6) | Full task | Full task |
| 8. Verify + verdicts (C4) | Yes | Yes |
| Output | Verdicts + reasons (enough for triage) | Verdicts + full evidence, ready for a research package |

Results are stored on the candidate. A candidate that passes light evaluation is **upgraded** to full evaluation. Upgrading reuses everything already gathered and only adds more search, reading and Skeptic rounds.

## 2. Workflow A: Evaluate My Research Idea

```text
INTAKE ──► CLARIFY (optional, human) ──► candidate v1 (origin: user)
   │
   ▼
FULL EVALUATION (section 1)
   │
   ▼
TRIAGE (rules, section 4)
   ├─ Pursue ───────────────────────────────────────────► REPORT
   ├─ Refine (fixable issue) ─► Scout: Refine ─► candidate v2 ─► re-evaluate affected
   │                                                    dimensions + quick novelty ─► TRIAGE
   └─ Reconsider ─► Scout: Alternatives (3–5, from this investigation's corpus)
                       ─► LIGHT EVALUATION each ─► best 1–2 ─► FULL EVALUATION ─► REPORT
REPORT ──► (user) PROPOSAL (A7) / export
```

**Step details:**

1. **Intake:** validate inputs, detect complexity/constraint conflicts (doc 02), create the investigation.
2. **Clarify:** A1 returns facets + questions. The UI waits for the user to confirm, edit or skip. When skipped, the stated assumptions are recorded.
3. **Full evaluation** of v1.
4. **Triage** by rules.
5. **Report.** Even when the user's idea fails, the report shows exactly why (closest works, facet matrix) and the best alternatives.

## 3. Workflow B: Find Me a Research Idea

```text
INTAKE ──► FIELD BRIEF (A1) ──► CLARIFY (optional, human)
   │
   ▼
LANDSCAPE RETRIEVAL (C2): recent papers (last ~3 years), surveys/reviews,
   highly cited recent work, latest preprints, per subtopic. ~300 screened.
   + SIGNALS (C3): GitHub, Hugging Face datasets, Hacker News, news (tier 3)
   │
   ▼
LANDSCAPE READING (A3): ~40–50 papers (cached analyses reused)
   │
   ▼
OPPORTUNITY MINING (code): landscape map, limitation clusters,
   contradiction pairs, method × problem matrix, dataset hints
   │
   ▼
GENERATE (Scout: Discover) ──► 8–12 candidates
   │
   ▼
PRE-SCREEN (code + A6 quick feasibility): grounding check, dedup,
   hard-constraint filter, diversity ──► keep up to 6
   │
   ▼
LIGHT EVALUATION (parallel, concurrency 2–3) ──► TRIAGE
   ├─ reject (reason recorded, shown to the user)
   ├─ refine ─► Scout: Refine ─► light re-evaluation (counts against limits)
   ├─ advance
   └─ too few advanced and regeneration budget left ─► Scout: Replenish ─► PRE-SCREEN
   │
   ▼
FULL EVALUATION of top ≤3 advanced candidates ──► TRIAGE ──► RANK
   │
   ▼
PRESENT: opportunity cards (ranked) + rejected candidates with reasons
   │
   ▼ (user selects)
PROPOSAL (A7) for the selected candidate  and/or  DEEP DIVE (Workflow A on it)
```

**Ranking finalists.** No single numeric score is shown. Finalists are ordered by rules:

1. Recommendation: Pursue before Refine.
2. Novelty category: Supported before Potential.
3. Feasibility: Feasible before Feasible with adjustments.
4. Significance level.

Ties are broken by evidence strength (number of verified supporting quotes). The UI shows the ordering reasons.

## 4. Triage rules (shared by both workflows)

Triage runs after each evaluation. The first rule that matches applies.

| # | Condition (from verdicts) | Action |
|---|---|---|
| 1 | A hard constraint is violated (feasibility blocking requirement that can't be simplified) | **Reject** ("infeasible under your constraints") |
| 2 | Novelty = *Previously investigated* **and** the closest work leaves a facet open | **Refine once** (differentiate on the open facet); otherwise **reject** |
| 3 | Novelty = *Previously investigated*, no open facet | **Reject** (Mode 1: trigger Alternatives) |
| 4 | Novelty = *Incremental variation* | **Refine once** to increase the contribution; if it's still incremental, reject (Beginner level: may be kept if it meets the meaningful-minimum standard, labelled as incremental) |
| 5 | Feasibility = *Not feasible* with suggested simplifications | **Refine** (simplify), then re-check feasibility **and** run a quick novelty check, because simplified ideas often collide with existing work |
| 6 | Significance = *Weak* | **Reject** (Mode 1: report with alternatives) |
| 7 | Novelty = *Insufficient evidence* | If budget allows, **expand search** once (more queries/sources); otherwise keep it, flagged "needs further investigation" |
| 8 | A Skeptic challenge refuted a key claim | Re-run the novelty assessment with the challenge, then re-apply triage |
| 9 | Otherwise | **Advance** (light → full) or **finalize** (full) |

Every triage decision is stored with its rule number and the evidence IDs behind it. That record is the "why" behind each card in the UI.

## 5. Candidate lifecycle

```text
proposed ─► screened_out (pre-screen; reason)
   │
   ▼
light_evaluating ─► rejected (reason) | needs_refinement ─► (new version) proposed
   │
   ▼
advanced ─► full_evaluating ─► finalist | rejected | needs_refinement
   │
   ▼
selected (by user) ─► proposal_ready | deep_dive
```

Each refinement creates a **new version** linked to its parent. The parent stays visible, so the user sees how an idea evolved and why.

## 6. Budgets, limits and stopping conditions

These are default values to tune with real runs. They're all configuration, not code.

| Limit | Mode 1 | Mode 2 |
|---|---|---|
| Papers screened (total) | 300 | 600 (landscape 300 + candidates) |
| Papers read (new analyses, not cached) | 40 | 120 |
| Candidates generated | 3–5 alternatives if needed | 8–12 (+ ≤1 replenish round of ≤6) |
| Light evaluations | ≤5 | ≤8 |
| Full evaluations | 1 (+≤2 alternatives) | ≤3 |
| Skeptic rounds | 3 (full) / 1 (light) | same |
| Refinements per candidate | ≤2 | ≤2 (≤4 per investigation) |
| Scout tool calls per task | ≤8 | ≤8 |
| Skeptic searches per round | ≤6 | ≤6 |
| LLM cost cap | ~$5 | ~$12 |
| Wall-clock cap | ~15 min | ~30 min |

**Stopping conditions:**

- **Skeptic loop:** saturation (a round finds 0 new relevant papers), the round limit, or the budget runs out.
- **Mode 2 overall:** the requested number of finalists is reached, or the budget is exhausted, or candidate generation is exhausted (replenish limit used).
- **Budget exhaustion is never silent.** The run finishes with what it has. Verdicts degrade to "insufficient evidence" or "needs further investigation" where coverage is low, and the report says which budget stopped it.

Budgets are enforced in two places:

1. The orchestrator checks counters before each stage.
2. The LLM callback refuses calls once the cost cap is reached.

## 7. Human-in-the-loop points

| Point | What the user can do | Default if skipped |
|---|---|---|
| Clarify (both modes) | Answer questions; edit facets or the field brief | Proceed with the recorded assumptions |
| After the Mode 2 presentation | Select an opportunity; reject one; ask the Scout for more in a direction ("more like this", "simpler") | Nothing more happens until the user acts |
| Refinement suggestions | Accept a proposed refinement of their own idea (Mode 1) | Eureka shows the refinement as a suggestion; it doesn't change the user's idea itself |
| Proposal | Request generation; regenerate a section | — |

## 8. Progress events

Every stage transition emits an event: stage name, counts (screened/read/candidates), the current candidate, cost so far, and messages like "Skeptic round 2 found 1 new relevant paper". The frontend shows these live (doc 11, doc 12).

## 9. Resumability and failure handling

- After each stage, the orchestrator persists its state (stage, counters, candidate statuses). After a crash or redeploy, the investigation resumes from the last completed stage.
- Stage-level failure handling is described in doc 06 (sources), doc 07 (verification) and doc 08 (LLM errors). The orchestrator's rule: **a failing component degrades the result; it doesn't crash the investigation.** Where it matters, the failure is visible in the coverage report.

## 10. Mapping to ADK

| Concept | ADK building block |
|---|---|
| Orchestrator | Custom agent with the state machine in its run logic |
| A1, A3, A4, A6, A7 | LLM agents with an output schema |
| A2 Scout, A5 Skeptic | LLM agents with function tools |
| Skeptic ⇄ Novelty rounds | Loop agent (max iterations, early exit on saturation) |
| Per-paper reading, per-candidate light evaluation | Async fan-out with semaphores inside the custom agent |
| State | ADK session (IDs + stage) backed by Postgres; domain data in our tables |
| Logging / cost / budget | Before/after-model callbacks |
