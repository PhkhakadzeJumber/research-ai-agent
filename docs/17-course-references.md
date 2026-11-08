# 17: Course References

The course repository (`ai-llm-course`) is a **read-only reference**. We learn from its examples and re-implement the ideas in this repository. **We never change the course repository.**

Note: the course's ADK examples use Gemini. Eureka uses Claude, so the ADK patterns apply but the model setup differs (doc 08, section 9).

| Course folder | What Eureka takes from it | Where in Eureka |
|---|---|---|
| `1. intro` | Transformer basics: background for the report | — |
| `3. context-memory` | Conversation memory patterns | Ask panel, clarification (doc 08 §10) |
| `4. logging-llm` | Logging every LLM interaction | `llm_calls` table, callbacks (doc 08 §7, doc 11 §6) |
| `5. vector-databases-embeddings` | Embeddings and vector search basics | Embedder, pgvector (doc 06 §2.4–2.5) |
| `7. rag` | The basic RAG pipeline, chunking | Passage retrieval (doc 06 §2) |
| `8. langchain-fundamentals` | Loaders, text splitters | Full-text chunking ideas (we don't use LangChain) |
| `9. advanced-rag` (query expansion, reranking) | Multi-query expansion; reranking | A1 query plans; relevance screening (doc 06 §2.1–2.2) |
| `10. LangGraph` (conditional loops, iterative review, approval interrupts, persistence) | Loop and human-in-the-loop patterns; checkpointing ideas | Skeptic loop, triage loop, clarification checkpoint, resumable stages (doc 05) |
| `11. MultiAgent` (tool error handling, supervisor, map-reduce, blackboard, research system) | Tool error dicts; map-reduce over papers; blackboard ≈ evidence ledger; research pipeline structure | Tools (doc 09), per-paper fan-out, the ledger (doc 07) |
| `12. ADK` (tools, sequential, parallel, sessions, callbacks, deploy) | Agent definitions, output keys, session state, callbacks, Cloud Run deployment | Agents (doc 04), orchestration mapping (doc 05 §10), observability, spike deployment (doc 14) |
| `13. claude-code` | Using Claude Code to build the project: a CLAUDE.md for this repo, code review on PRs, skills | Development workflow |
