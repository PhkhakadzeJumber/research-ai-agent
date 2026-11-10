# 09: Tools and MCP

## 1. What a "tool" is in Eureka

A tool is a function that an agent (the Scout or the Skeptic) can decide to call during its reasoning loop. In ADK, a tool is a plain Python function: its name, typed parameters and docstring are shown to the model, so **the docstring is the tool's instructions**.

Only the two true agents (A2 Scout, A5 Skeptic) get tools. All the other LLM components receive prepared context and make no tool calls. That keeps costs predictable and behavior testable.

## 2. Tool catalog

| Tool | Purpose | Main inputs | Returns | Used by | Budget |
|---|---|---|---|---|---|
| `search_papers` | Search academic sources | Query, optional year range, optional source list, limit | Paper IDs + title, year, venue, abstract snippet, tier | Scout, Skeptic | Scout ≤5 per task; Skeptic ≤6 per round |
| `get_paper_analysis` | Get (or create) the structured analysis of a paper | Paper ID | The PaperAnalysis record (triggers the Paper Reader if not cached) | Scout, Skeptic | Counts against the read budget |
| `get_passages` | Get the most relevant passages from a paper for a question | Paper ID, question | Passages with chunk IDs and source level | Scout, Skeptic | — |
| `get_citations` / `get_references` | Snowball from a paper | Paper ID, limit | Paper IDs | Skeptic | ≤3 per round |
| `lookup_datasets` | Check dataset existence/availability | Dataset names or task description | Matches with link, license, size, availability | Scout, A6 (called by the orchestrator) | — |
| `get_signals` | Get tier-3 signals for a topic | Topic, source list | Signal records (labelled "not evidence") | Scout | ≤3 per task |

**Design rules for every tool:**

1. **Return errors as data:** a status (success/error) plus an error message, instead of raising exceptions. The model can then explain or retry differently. This is the same pattern as the course's weather-tool examples.
2. **Return IDs, not free text, for papers.** Agents can only cite what a tool returned.
3. **Small, focused outputs.** Return snippets and IDs; the agent asks for passages when it needs them. This protects the context window.
4. **Enforce budgets inside the tool.** Once the budget is used up, the tool returns "budget exhausted" as data, and the agent must conclude with what it has.
5. **Idempotent and cached.** The same call returns the cached result.
6. **Log every call:** tool name, arguments, result size, latency, budget remaining.
7. **No side effects outside Eureka.** No tool sends messages, writes files or acts on external systems.

## 3. Where MCP fits

**MCP (Model Context Protocol)** is an open standard for exposing tools and data to AI applications through a server process. It's worth using where it adds real value, and not everywhere.

### 3.1 Decision: don't put MCP in the core path for the MVP

| Option | Assessment |
|---|---|
| Run the core literature tools as an MCP server and call them over MCP from our own agents | Adds a process boundary, serialization and deployment work, with no benefit while the agents and tools live in the same service. **Not for the MVP** |
| Consume third-party MCP servers (e.g. a GitHub MCP server) in the Scout through ADK's MCP toolset support | Possible for signal sources. But our need is small (search repos/issues), and a direct HTTP adapter is simpler to test and rate-limit. **Optional, later** |

### 3.2 Where MCP adds value: expose Eureka *as* an MCP server (post-MVP extension)

Build a small **"eureka-mcp" server** that exposes Eureka's capabilities to any MCP client (Claude Desktop, Claude Code, other agents):

| MCP tool | What it does |
|---|---|
| `search_papers` | The deduplicated multi-source search |
| `get_paper` | Metadata + analysis for a paper ID / DOI |
| `check_novelty` | Starts a light evaluation for an idea and returns the verdict + closest works |
| `get_investigation` | Returns the verdicts, evidence and coverage of an investigation |

Why it's worthwhile:

1. **Developer productivity.** While developing, you can use Eureka's search from Claude Code.
2. **Interoperability.** Other agents (or other student projects) can reuse Eureka.
3. **A clean course demonstration** of MCP, with a real purpose.

It reuses the same service functions as the tools in section 2, so it doesn't duplicate any logic.

### 3.3 Safety for MCP

- The MCP server requires an API token, and each tool has rate limits.
- `check_novelty` runs with a strict budget, so outside clients can't run up costs.
- The same evidence rules apply: results contain IDs and verified metadata only.

## 4. How tools relate to the orchestrator

- The orchestrator does the **planned** retrieval (landscape, targeted evaluation retrieval).
- Agents use tools for **opportunistic** retrieval (the Skeptic's adversarial searches, the Scout's quick checks).
- Both paths go through the same Literature Discovery service. So cache, dedup, rate limits and coverage accounting are shared, and **every search, whoever made it, appears in the coverage report.**
