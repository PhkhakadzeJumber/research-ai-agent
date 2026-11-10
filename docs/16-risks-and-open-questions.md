# 16: Risks and Open Questions

## 1. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | Retrieval misses the key prior paper (different terminology) | False "possibly unexplored". The worst failure | Medium | Query-diversity quotas; Skeptic's adversarial search; snowballing; recommendations; saturation stop; conservative verdict rules; coverage report |
| 2 | Mode 2 produces plausible but ungrounded ideas | Credibility | Medium | Opportunity mining gives the Scout real material; ≥2 verified inspiration quotes required; planted-candidate and grounding metrics |
| 3 | Mode 2 cost/latency too high (many candidates × evaluation) | Budget overrun, slow demo | High | Light vs full evaluation; idea-agnostic paper-analysis cache; budgets; caps; cheaper reader model option; parallelism with limits |
| 4 | API rate limits (especially Semantic Scholar, GitHub search) | Slow/failed runs | High | API keys; cache; per-source limiters; multi-source redundancy; pre-warmed demo cache |
| 5 | Fabricated citations/quotes | Credibility | Medium (without controls) | IDs only from the DB; quote verification; references rendered by the backend; hallucination counter (target 0) |
| 6 | Self-agreement / overconfidence | Unsupported verdicts | Medium | Independent Skeptic; code verdict rules; no percentages; generator ≠ critic |
| 7 | Endless loops (refine → reject → refine …) | Cost, time | Medium | Hard limits per candidate and per investigation; one replenish round; budget-aware tools |
| 8 | ADK + Claude integration quirks | Lost days | Medium | Week-1 spike; pinned versions; raw-SDK escape hatch |
| 9 | Paywalled full text | Shallow evidence | High | Open-access only; abstract-level labels; weaker verdicts when key evidence is abstract-only |
| 10 | Signals misused as evidence | Scientific reliability | Low (with controls) | Separate storage; verifier rejects tier-3 items in evidence; UI labels |
| 11 | Complexity "Beginner" yields trivial ideas | Usefulness | Medium | Meaningful-minimum standard; same evaluation as other levels; rubric check |
| 12 | Small evaluation set | Unconvincing results | High | Clever construction (known paper, date cutoff, planted candidates), ablations, honest framing |
| 13 | Scope creep (two modes, one person, 4 weeks) | Nothing finished | High | Strict MVP; weekly cut lines (doc 15 §2.5); feature freeze in week 4; Workflow A first, then B reuses it; Claude Code for boilerplate |
| 14 | Prompt injection via paper text | Manipulated outputs | Low | Delimiters, preamble rule, schema-only outputs, no dangerous tools, verifier |
| 15 | Cloud Run throttles background runs | Stuck investigations | Medium | CPU always allocated + min instances in demos; resumable stages; Cloud Tasks later |
| 16 | Key/account setup problems (e.g. key not workspace-scoped, Semantic Scholar key delay) | Blocked development | Medium | Day-1 checklist: workspace-scoped Anthropic key, Semantic Scholar key request, GCP project with billing |

## 2. Open questions

Decisions still to make. Recommendations are included.

| # | Question | Options | Recommendation |
|---|---|---|---|
| 1 | Demo domain for the MVP and evaluation | AI/ML/NLP · Robotics · Biomedical | **AI/ML/NLP**: arXiv full text, matches the course, familiar ground |
| 2 | Model for the high-volume roles (Paper Reader, relevance screening) | Opus 5.5 for everything · cheaper model (Haiku 5.5) | Decide after measuring extraction quality on 10 labelled papers (week 2) |
| 3 | Frontend stack | React · Streamlit | React only if already fluent; otherwise **Streamlit** for the MVP (recommended solo; see doc 15 cut lines) |
| 4 | Embeddings in production | Local SPECTER2 · Vertex AI embeddings | Vertex AI in the cloud (syllabus + no model in the container); local for development/tests |
| 5 | LLM billing | Anthropic API key · Claude on Vertex AI (Google Cloud credits) | Anthropic key by default; Vertex if you have education credits |
| 6 | Should Mode 2 auto-generate a proposal for the top finalist? | Auto · on request | On request (saves cost; the user picks) |
| 7 | How many finalists by default? | 2 · 3 · 5 | 3 |
| 8 | Which signal sources in the MVP? | HF + GitHub · + Hacker News · + RSS | HF + GitHub |
| 9 | Should rejected candidates be shown by default? | Shown · collapsed · hidden | Collapsed but visible (transparency) |
| 10 | Authentication for the demo | None · Firebase Auth · IAP | IAP or Firebase Auth; never fully public without per-user caps |
| 11 | Budget defaults (doc 05) | — | Start with the table in doc 05; tune after the first 10 real runs |
| 12 | Coverage thresholds for "Supported opportunity" | — | ≥150 screened, ≥25 read, ≥3 sources, saturation; revisit after the evaluation |
| 13 | Second rater for the human evaluation | Supervisor · classmate · none | Ask a supervisor or classmate to rate a subset blind; otherwise report the single-rater limitation (doc 13) |
| 14 | Language support | English only · multilingual | English only for the MVP; listed as a blind spot |

## 3. Assumptions we're making

- One person builds Eureka, in ~4 weeks, with Claude Code as a development aid.
- A Google Cloud project with billing (or credits) is available.
- The Anthropic budget is enough for development + evaluation (estimate it after week 1 measurements).
- Academic APIs stay accessible under their current free terms.
- The demo uses English-language literature in AI/ML/NLP.
