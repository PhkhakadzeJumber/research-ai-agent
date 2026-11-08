# 01: Product Vision

## 1. The problem

Choosing a research direction is high-stakes and poorly supported:

- Students often spend weeks or months on an idea before discovering that it was already done, is too ambitious for their resources, or isn't important enough to matter.
- Students without an idea don't know where real open problems are. They end up with topics that are vague, already saturated, or chosen only because they sound modern.
- General-purpose LLMs make this worse in a subtle way. They answer fluently, they overlook existing work, they call ideas "novel" too easily, and they sometimes cite papers that don't exist. A human study (Si et al., 2024, *"Can LLMs Generate Novel Research Ideas?"*) found that LLM-generated ideas were rated *more novel* but *less feasible* than expert ideas, and that LLMs are unreliable judges of novelty.

## 2. What Eureka is

Eureka is a research **discovery and validation** agent with two modes:

1. **Evaluate My Research Idea:** investigate an idea the user already has.
2. **Find Me a Research Idea:** discover grounded research opportunities in a field the user chooses, then evaluate them.

In both modes the output isn't prose. It's a **structured research workspace**: verdicts, the evidence behind them, what was searched, what remains uncertain, and an actionable research package.

## 3. Who it's for

| User | Typical need | What changes for them |
|---|---|---|
| Undergraduate student (primary demo user) | Needs a thesis or course project topic that is doable in 3–6 months on a laptop or free GPU | Mode 2 with Beginner/Intermediate complexity; strict feasibility checks |
| Master's student | Has a rough idea and needs to know whether it's novel enough for a thesis | Mode 1 with Intermediate/Advanced complexity |
| Researcher | Wants a fast prior-work and counterevidence check before investing | Mode 1, deeper search budgets, Advanced complexity |
| Supervisor | Wants to check a student's proposal quickly | Mode 1, evidence explorer, exported report |

## 4. What Eureka is not

- **Not an originality certificate.** It reports what it searched and found. The researcher and their supervisor make the call.
- **Not a chatbot.** Conversation exists (clarifying questions, follow-up questions about an investigation), but the product is a workspace.
- **Not a paper writer.** It produces a research proposal and a plan, not a paper.
- **Not a general web researcher.** Academic literature is the evidence. Web, GitHub and news sources are only used to spot problems and motivation (tier 3).

## 5. Existing tools and research agents

> These entries are from the team's background knowledge. Verify each one before citing it in our final report, which is Eureka's own rule applied to us.

| Tool / system | Strength | Gap Eureka addresses |
|---|---|---|
| Elicit | Paper search + structured extraction tables | Built for literature review, not for deciding whether an idea is novel; no adversarial step; no idea discovery from limitations |
| Consensus | Answers questions from papers | Answers questions; doesn't evaluate a proposed contribution |
| scite | Citation statements labelled supporting or contrasting | Looks at single papers, not an idea against the whole field |
| Semantic Scholar | Huge index, citation graph, TLDRs, API | A data source, not an evaluator. **We build on it.** |
| Connected Papers / ResearchRabbit / Litmaps | Visual citation-graph exploration | The user does all the reasoning. Inspiration for our graph view |
| Undermind | Deep iterative semantic search | Strong search, but no verdicts, feasibility check or idea discovery |
| Deep-research modes in ChatGPT / Gemini / Perplexity | Long multi-step reports | Mix web and papers; citation-quality issues; tend to call ideas novel; don't report what they failed to search |
| PaperQA2 (FutureHouse, 2024) | High-accuracy RAG over full text, contradiction detection | Answers questions; not built around ideas. **A design reference for evidence handling** |
| OpenScholar (AI2, 2024) | Open retrieval-augmented literature synthesis with citations | Synthesis, not falsification or discovery |
| The AI Scientist (Sakana, 2024) | Generates ideas, with an automated novelty check | The novelty check is shallow (a few queries + a yes/no). Eureka's main improvement target |
| AI co-scientist (Google, 2025) | Multi-agent generate / debate / evolve hypotheses | Huge compute, not reproducible at student scale. **We borrow the generator-vs-critic idea in a bounded form** |
| ResearchAgent (Baek et al., 2024) | Iterative idea generation with reviewing agents | Weaker grounding in verified evidence; no user constraints |

## 6. How Eureka differentiates

1. **Discovery grounded in literature, not brainstorming.** Mode 2 ideas come from mined limitations, future-work statements, contradictions between findings, and gaps in method × problem combinations. Each one cites the evidence that inspired it.
2. **Generator and critic are separated.** The agent that proposes ideas (the Scout) is never the agent that judges them. The Skeptic tries to refute them with its own searches.
3. **A facet-level prior-work matrix.** It shows *which part* of an idea is covered by which paper, with quotes.
4. **A coverage report and saturation.** Eureka says how hard it looked and when it stopped finding new papers.
5. **Rejections are shown openly.** In Mode 2 the user sees rejected candidates and the reasons, which builds trust and teaches research judgment.
6. **Complexity and constraints are first-class inputs.** An easy project is still scientifically meaningful, and an undergraduate never gets "pretrain a large model from scratch".
7. **Verified citations.** References come only from database records, and quotes are string-matched against the stored text.

## 7. Design decisions that challenge the original brief

| Brief said | We decided | Why |
|---|---|---|
| 8 agents (+ the Scout = 9) | **7 LLM components + 4 code components** (doc 03). Literature Discovery is a code service; the Opportunity Generator is merged into the Scout | Code is more reliable and testable for search, ranking, verification and orchestration. One generative agent avoids two agents doing the same job |
| An LLM orchestrator resolves conflicts | **The orchestrator is code**; conflicts are resolved by rules over evidence | Reproducible, testable, explainable |
| Confidence scores | **Verdict categories with written rules + coverage stats** | Percentages would be uncalibrated, and so misleading |
| Full-text PDFs everywhere | Abstracts for everything, full text for the top open-access papers, and every claim labelled with its source level | Most papers aren't open access, and PDF parsing is a time sink |
| A knowledge graph | A view rendered from relational tables; no graph database | Same user value, far less infrastructure |
| GitHub / news / communities as research sources | Allowed **only as tier-3 signals** (motivation, practical problems), never as novelty evidence | Scientific reliability requirement |

## 8. Success criteria for the course project

1. Both modes work end to end on a deployed instance.
2. Measured improvement over two baselines (plain Claude; Claude + basic RAG) on our evaluation set. Where a baseline ties or wins, we say so.
3. Zero fabricated references in evaluation runs (checked automatically).
4. A Mode 2 run for an undergraduate profile produces at least 2 candidates that a human reviewer rates as grounded and feasible.
5. Clean architecture that the team can explain: every agent's existence is justified.
