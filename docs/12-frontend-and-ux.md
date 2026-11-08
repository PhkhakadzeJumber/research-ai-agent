# 12: Frontend and UX

## 1. Principles

- **A research workspace, not a chat.** Results are structured views: cards, matrices, evidence lists.
- **Transparency.** Every verdict can be opened to see its evidence, its rule trace and the coverage behind it. Rejected candidates stay visible, with their reasons.
- **Honest labels.** Evidence, interpretation, hypothesis and signal look visibly different. Abstract-only evidence and preprints carry badges.
- **Progress is visible.** Runs take minutes; the user always sees what's happening.
- **Clean and academic.** Clarity over decoration; no unnecessary animation.

## 2. Technology

React + TypeScript + Vite, Tailwind CSS + a component library (e.g. shadcn/ui), TanStack Query for data fetching, the native EventSource API for SSE, Cytoscape.js for the evidence graph, and a simple chart library for funnels and trends.

Alternatives: Next.js (server rendering isn't needed); Streamlit (fastest to build, but looks like a demo and handles a multi-view workspace poorly). **Fallback:** if no one on the team is comfortable with React, use Streamlit for the MVP and accept the UX limits.

## 3. Screens

### 3.1 Home: two entry points

Two large cards side by side:

- **Evaluate My Research Idea:** "You have an idea. Eureka checks what's been done, how novel and feasible it is, and how to make it stronger."
- **Find Me a Research Idea:** "You have a field and interests. Eureka discovers grounded research opportunities and evaluates them for you."

Below the cards: recent investigations, and a link to edit the constraints profile.

### 3.2 New investigation forms

| Mode 1 form | Mode 2 form |
|---|---|
| Idea text (large field, with examples) | Field picker (chips + free text) |
| Field (optional) | Interests / subtopics |
| Known related papers (optional, DOI/arXiv/title) | Preferred technologies, application domain, exclusions |
| | Number of opportunities wanted |

**Shared panel (both forms):**

- **Complexity selector:** three cards (Beginner / Intermediate / Advanced), each with a short description from doc 02.
- **Constraints panel:** prefilled from the profile; editable for this run; hard constraints marked.
- A warning appears if complexity and constraints conflict, with the two resolution options.
- An estimated duration and cost range is shown before starting.

### 3.3 Clarification

Shows Eureka's structured reading:

- **Mode 1:** editable facets.
- **Mode 2:** an editable list of subtopics.

Below it, up to 3 questions. Buttons: "Continue", or "Skip and use these assumptions".

### 3.4 Progress view

- **Stage rail:** the workflow stages with done / running / pending states, and the current candidate.
- **Live counters:** papers screened/read, candidates proposed/rejected/advanced, Skeptic round, cost so far.
- **Activity feed:** human-readable events ("Skeptic found a closely related 2023 paper; re-checking novelty").
- **Mode 2 funnel chart:** papers → limitation clusters → candidates → light-evaluated → finalists.
- Partial results appear as they become available (candidate cards fill in).

### 3.5 Mode 2: candidate board

- **Candidate cards** (finalists first, then advanced, then rejected/screened-out in a collapsible section). Each card shows:
  - title and research question
  - origin strategy badge (limitation / contradiction / combination / …)
  - verdict chips: novelty category, significance level, feasibility level, recommendation
  - one-line motivation, with 1–2 inspiration evidence chips
  - complexity fit
  - status
  - for rejected cards: the rejection reason and the evidence ("Closely matches Smith 2024 on problem, method and dataset")
- **Card actions:** View details · Compare · Deep dive · Generate proposal · More like this · Simpler · Exclude.
- **Lineage:** refined candidates show "refined from v1 because …".

### 3.6 Candidate comparison

A side-by-side table for 2–4 candidates. Rows:

- research question
- each verdict, with its reason
- closest work
- datasets
- compute tier
- estimated time
- key risks
- evidence counts
- coverage level

### 3.7 Candidate / investigation workspace (both modes)

Tabs:

| Tab | Content |
|---|---|
| **Overview** | Verdict cards (category + reason + top evidence + uncertainties), recommendation with rule trace, coverage summary |
| **Prior work** | Facet × paper matrix with colored cells; hover shows the quote; click opens the paper |
| **Papers** | Library: filters (relevance, year, tier, full text/abstract, found by analyst/Skeptic/Scout); each row expands to the analysis and the quotes used |
| **Evidence** | Claims grouped by kind; each claim shows its supporting/contradicting quotes, status and statement type |
| **Skeptic log** | Rounds, queries, challenges, resolutions, saturation curve |
| **Feasibility** | Requirements vs the user's constraints (check marks / blockers), datasets with availability, timeline estimate, simplifications |
| **Graph** | Evidence graph (optional, week 4) |
| **Proposal** | The research package (17 sections) with evidence-status tags and references; regenerate a section; export |

### 3.8 Mode 2 landscape view (optional)

Subtopic map with trend sparklines, recurring limitation clusters, contradiction pairs, combination hints. Useful for teaching and transparency.

### 3.9 Ask panel (optional)

A side panel in the workspace for questions about the investigation. Answers carry citation chips. Suggested questions include "Why was candidate 3 rejected?" and "What would make this feasible on a laptop?".

### 3.10 Exports

- **Investigation report:** Markdown / PDF.
- **Research package:** Markdown / PDF; Word later.

Exports include the coverage report and the "not a guarantee of originality" note.

## 4. Visual language

| Element | Convention |
|---|---|
| Novelty categories | Neutral colors, never a "green = original" signal: previously investigated (grey), incremental (amber), potential (blue), supported (teal), insufficient evidence (hatched) |
| Statement types | Evidence (plain + citation chip), interpretation (italic label), hypothesis (dashed border), signal (labelled "community signal, not evidence") |
| Badges | "Abstract only", "Preprint", "Full text" |
| Rejected candidates | Muted, but readable, with the reason visible |

## 5. MVP vs later

| MVP (weeks 2–3) | Week 4 if time allows | Later |
|---|---|---|
| Home with two entry points; both forms; complexity + constraints | Comparison view | Ask panel |
| Clarification | Proposal view + Markdown export | Landscape view |
| Progress view (stage rail, counters, feed) | Evidence graph | PDF/Word export polish |
| Candidate board with cards + rejection reasons | Funnel chart | Collaboration, sharing links |
| Workspace tabs: Overview, Prior work, Papers, Evidence, Skeptic log, Feasibility | | Saved profiles for teams |
