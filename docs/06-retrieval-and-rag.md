# 06: Retrieval and RAG

RAG in Eureka has one job: **retrieve the evidence that a specific claim depends on**. That means the papers and passages that support or contradict it, not general background. This document covers both evidence retrieval (academic sources) and signal retrieval (discovery sources).

## 1. Academic sources (evidence, tiers 1–2)

| Source | Role | Access notes | MVP? |
|---|---|---|---|
| **Semantic Scholar Graph API** | Primary search; citation and reference graph (snowballing); recommendations; TLDRs; open-access PDF links | Free API key (request early; approval can take days). Tight rate limits, so cache everything | Yes |
| **OpenAlex** | Second search index; topics/concepts; per-year publication counts for a query (trend signal); open-access locations; DOI metadata | Free. Identify yourself with an email for the polite pool. Check current rate-limit and key policy before building | Yes |
| **arXiv API** | Latest preprints; **reliable full-text PDFs** for CS/ML/physics/math | Free. Slow request rate (roughly 1 request every 3 seconds); cache | Yes |
| **Crossref** | DOI validation and metadata repair for references in reports | Free; polite pool with email | Yes (verification only) |
| **PubMed (E-utilities)** | Biomedical literature | Free; API key recommended | Later, unless the demo domain is biomedical |
| CORE / Unpaywall | More open-access full text | Free keys | Later |

**MVP decision:** one demo domain, **AI/ML/NLP** (abundant arXiv full text, matches the course). The adapters stay generic.

**Tiers:**

- Peer-reviewed venue (from venue metadata): **tier 1**.
- arXiv/preprint without a known venue: **tier 2**.

Both count as evidence. The UI shows "preprint" badges, and the verdict rules record the tier mix in the coverage report.

## 2. Evidence retrieval pipeline

```text
Query plan (A1 / Scout / Skeptic)
   │
   ▼
Source adapters (async, per-source rate limiter, retry with backoff, cache)
   │
   ▼
Normalize ─► Paper record { ids: doi / arxiv / s2 / openalex, title, abstract, year,
                            venue, authors, citation count, open-access PDF link, tier }
   │
   ▼
Dedup: exact ID match ─► fuzzy match (normalized title + year ±1 + first author)
   │
   ▼
Snowball (full evaluation): references + citations of the top ~10 (Semantic Scholar)
   │
   ▼
Embed title + abstract ─► store vector
   │
   ▼
Score: similarity to each facet and to the whole idea, + small citation/recency prior
   │
   ▼
LLM relevance screening of the top ~60 (batched; 0–3 scale with a one-line reason)
   │
   ▼
Select for reading ─► Paper Reader (cached per paper)
   │
   ▼
Full text for the top ~10 open-access papers ─► parse ─► chunk ─► embed chunks
   │
   ▼
Passage retrieval for agents: hybrid (vector similarity + keyword full-text search)
```

### 2.1 Query diversity: the main defense against missed prior work

Missing a paper because it uses different words is the most dangerous failure (it leads to a false "novel"). Defenses:

- A1 query plans have **quotas**: terminology variants, older terms, facet-only queries, method-free queries, and method-in-another-domain queries.
- The Skeptic writes queries from *other communities' vocabulary* (e.g. statistics vs ML terms for the same concept).
- **Citation snowballing** finds papers that share no keywords but cite or are cited by the closest works.
- Semantic Scholar **recommendations** for the closest papers act as another non-keyword path.

### 2.2 Ranking

1. **Embedding similarity:** maximum similarity across facets (so a paper matching one key facet strongly isn't diluted), plus similarity to the whole idea.
2. **A small prior** for citation count (log-scaled) and recency. Kept small so obscure but exact matches aren't buried.
3. **LLM relevance screening** on the top ~60. This is a high-volume role; doc 08 discusses a cheaper model for it. Cohere rerank (used in course lesson 9) is an alternative that needs another vendor key.

### 2.3 Full text

- **Which papers:** the top ~10 by relevance that are open access (arXiv first, then open-access links from Semantic Scholar/OpenAlex).
- **Parsing:** a PDF text extractor in the MVP, with section headings kept. GROBID (better section and reference parsing) is a later upgrade.
- **Chunking:** section-aware chunks of about 800 tokens with ~100 tokens of overlap. Each chunk keeps its section title (limitations and future-work sections are especially valuable to the Scout).
- **Legal:** only open-access sources, and robots/terms respected. No paywall circumvention. Store text for internal retrieval only; never republish it.
- **Labelling:** every quote carries its source level (full text vs abstract). If only the abstract was available, the UI and the report say so.

### 2.4 Embeddings

| Option | Pros | Cons |
|---|---|---|
| **SPECTER2** (local, trained on scientific citation data) | Strong for paper-to-paper similarity; free; offline | Needs CPU/GPU in the container; query–paper similarity needs the right adapter |
| A general local model (e.g. bge-small) | Simple, fast | Weaker on scientific text |
| **Vertex AI text embeddings** | Managed, scalable; meaningful Vertex AI usage without Gemini | Costs per call; needs a GCP project |

**Decision:** an embedder interface with two implementations: local (development, tests) and Vertex AI (deployment). **Never mix vectors from different models in one index.** Every stored vector records its model ID, and a model change triggers re-embedding.

### 2.5 Vector storage

**PostgreSQL + pgvector**, in the same database as everything else. A per-investigation corpus is hundreds to a few thousand vectors, far below what needs a dedicated vector DB. Chroma (used in the course) works locally, but we would still need a relational DB for the evidence ledger. Vertex AI Vector Search is overkill at this scale.

## 3. Discovery sources (signals, tier 3), used by the Scout only

These help find **real-world problems and emerging directions**. They are **never** used as evidence for novelty, significance or any verdict.

| Source | What we take | Access | MVP? |
|---|---|---|---|
| **Hugging Face Hub** (datasets, papers, models) | Datasets per task (feeds feasibility); dataset gaps (tasks/languages with few datasets); trending models | Public API; token optional | **Yes**: also powers dataset lookup |
| **GitHub** | Popular repos per topic; recurring issues/feature requests in major libraries ("pain points") | REST/search API with a token; strict search rate limits | **Yes** (small scope) |
| **Hacker News** (Algolia search API) | Practitioner discussions of problems with a technology | Free, no key | Optional |
| **Tech news / industry blogs (RSS)** | Emerging developments | RSS feeds from a curated list | Optional |
| Reddit, Stack Overflow, forums | Practitioner pain points | Restrictive API terms | Later / maybe never |
| Kaggle, OpenML, Zenodo | More dataset discovery | Keys / APIs | Later |

**Signal record:** source, URL, title, short summary, date, popularity metric where available, topic tags, tier 3.

**Guardrails:**

- Signals are stored separately from papers and **can't be cited** in claims. The verifier rejects any claim that uses a signal as supporting evidence.
- A candidate whose motivation comes only from signals is flagged "signal-only". It must get its scientific grounding from literature during evaluation, or it is rejected.
- Signals are summarized by code or a cheap model call. Their text goes into the Scout's context packet inside a clearly labelled "unverified signals" section.

## 4. Landscape retrieval for Mode 2

Mode 2 needs a picture of the field, not just a target. The landscape stage runs, per subtopic in the field brief:

- **Recent work:** last ~3 years, sorted by relevance.
- **Surveys and reviews:** queries with "survey"/"review"/"systematic review". Surveys contain explicit open-problem sections, which are high-value Scout material.
- **Recent highly cited work:** the papers that define the current state of the art.
- **Latest preprints:** the last few months from arXiv, for emerging directions.
- **Trend counts:** OpenAlex per-year counts for subtopic queries. A rising count means emerging; a flat or high count means mature. Shown in the landscape map; never a verdict input.

Then **opportunity mining** runs over the Paper Reader outputs (code with embeddings):

| Miner | How it works | Output |
|---|---|---|
| Landscape map | Cluster paper embeddings into subtopics; pick representative papers; attach trend counts | Subtopic clusters |
| Limitation clusters | Embed every limitation and future-work quote; cluster; keep clusters with ≥3 papers from ≥2 distinct author groups | "Recurring limitation" clusters with member quotes |
| Contradiction pairs | Compare findings that share a question/setting (embedding similarity) but have opposite directions; one cheap LLM check confirms each pair is a real contradiction | Contradiction pairs with quotes |
| Method × problem matrix | Normalize extracted methods and tasks; count co-occurrences; highlight empty cells whose row and column are both well populated | Combination hints |
| Dataset hints | Datasets mentioned per task, plus a Hugging Face availability check | Dataset table |

## 5. Caching, rate limits, retries

| Concern | Approach |
|---|---|
| API cache | An `api_cache` table keyed by source + normalized request. TTL: search 7 days, paper metadata 30 days, PDFs/full text indefinitely. This makes evaluations and demos repeatable and cheap |
| Paper analysis cache | One PaperAnalysis per paper per extractor version, reused across candidates and investigations |
| Rate limiting | A per-source async limiter matched to each source's documented limit; the same limiter is shared across concurrent investigations |
| Retries | Exponential backoff with jitter on 429/5xx/timeouts; honor Retry-After; maximum 3–5 attempts |
| Timeouts | Search ~20 s, PDF download ~30 s, per-stage timeouts in the orchestrator |
| Partial failure | If a source fails, continue with the others, record the failure in the coverage report, and lower the verdict strength if coverage falls below thresholds |
| Demo safety | Pre-warm the cache for the demo inputs the day before |

## 6. Quality checks for retrieval (tested in doc 13)

- Known-paper recall@k on the evaluation set.
- Dedup precision (no false merges of different papers) on a labelled set.
- How much query diversity (A1 quotas) and Skeptic searches add to recall (ablations).
