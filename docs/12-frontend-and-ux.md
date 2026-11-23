# 12: Frontend and UX

## 1. Principles

- **A research workspace, not a chat.** Results are structured views: cards, matrices, evidence lists.
- **Transparency.** Every verdict can be opened to see its evidence, its rule trace and the coverage behind it. Rejected candidates stay visible, with their reasons.
- **Honest labels.** Evidence, interpretation, hypothesis and signal look visibly different. Abstract-only evidence and preprints carry badges.
- **Progress is visible.** Runs take minutes; the user always sees what's happening.
- **Clean and academic.** Clarity over decoration; no unnecessary animation.

## 2. Technology

**Built (step 10):** Next.js (App Router) + TypeScript, Tailwind CSS with the Figma variables as design tokens, TanStack Query for data fetching, the native EventSource API for the live progress stream, types generated from the API's OpenAPI description (`openapi-typescript`), Vitest + Testing Library for the frontend's logic. No component library: the design's components (buttons, pills, cards, the nav) are hand-written to match Figma. Fonts are self-hosted by `next/font`.

**Why Next.js, not plain React + Vite:** Next forwards `/api/*` to the backend, so the browser only ever talks to one site and the login cookie (doc 11 §7) stays first-party; a cookie from another domain would be blocked by browsers. It also deploys to Vercel as planned (doc 14). Pages render in the browser (the data needs a login), so server rendering itself isn't used much. Checked in step 10a: the SSE stream passes through Next's forwarding as it happens.

Earlier alternatives: Streamlit (fastest, but looks like a demo and handles a multi-view workspace poorly).

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

### 3.10c Legal pages and footer ✅ step D

Figma 20: `/terms`, `/privacy`, `/refunds` (public, one layout with a side menu; the text in `components/legal.tsx` states what the site actually does: private uploads sent to the AI provider only with consent, only the card's brand, last four and expiry kept, the credit and refund rules of doc 18). A footer on every page links Pricing and the three.

### 3.10b Account emails ✅ step B

Figma 14–16: `/forgot-password`, `/reset-password?token=`, `/verify-email?token=` share the log-in layout (`AuthLayout`). Log in has "Forgot password?"; Sign up requires accepting the Terms and the Privacy Policy. Until the email is verified, a banner under the top nav offers "Resend the link", and a free evaluation explains it waits for the verification.

### 3.11 Plans and billing (doc 18 §6) ✅ step P4

**Checkout (step C; Figma 17–19):** Pricing → `/checkout?product=` (card form + order summary) → `/checkout/success` → Plan & billing → `/profile/billing/manage` (cancel / renew, card on file, invoices with receipts). The card number is checked in the browser (`lib/card.ts`: Luhn, expiry) and never sent; only brand, last four and expiry are. Checkout is open only for `EUREKA_DEMO_ACCOUNTS`; others see "Payments aren't open yet".

Built from Figma screens 12, 13 and 01b. What a plan allows is explained before the click (`lib/plan.ts`: `canStart`, `priceText`, `chipText`); the server still decides. Until payments (step P5), Go Pro, Manage billing and Buy credits say "Payments open soon".

- **Pricing** (`/pricing`, public): Free vs Pro, the credit price of each action, FAQ.
- **Profile → Plan & billing**: plan and renewal, credits left (monthly / top-up), credit history, Manage billing, top-up.
- **Start**: a 🔒 Pro badge on Mode 2 for free users; credit prices on the start buttons; "1 free evaluation left".
- **Nav chip**: credits left, or "Free · 1 evaluation left".
- No dollar amounts or model names on user pages (the upload consent still names the AI provider).

## 4. Visual language

| Element | Convention |
|---|---|
| Verdicts (as built, step 10c, `frontend/src/lib/format.ts`) | Green = good, amber = has problems, red = blocks it, grey = unclear: Pursue / Refine / Reconsider; Feasible / with adjustments / not feasible; Strong / Moderate / Weak |
| Novelty categories | Never a "green = original" signal: supported opportunity (indigo), potential opportunity (amber), incremental variation (grey), previously investigated (red), insufficient evidence (grey) |
| Statement types (research package) | Evidence (green), interpretation (indigo), hypothesis (violet), to verify (amber), each with its source chips |
| Statement types | Evidence (plain + citation chip), interpretation (italic label), hypothesis (dashed border), signal (labelled "community signal, not evidence") |
| Badges | "Abstract only", "Preprint", "Full text" |
| Rejected candidates | Muted, but readable, with the reason visible |

## 5. MVP vs later

| MVP (week 4 in the solo roadmap) | If time allows after the MVP | Later |
|---|---|---|
| Home with two entry points; both forms; complexity + constraints | Comparison view | Ask panel |
| Clarification | Proposal view + Markdown export | Landscape view |
| Progress view (stage rail, counters, feed) | Evidence graph | PDF/Word export polish |
| Candidate board with cards + rejection reasons | Funnel chart | Collaboration, sharing links |
| Workspace tabs: Overview, Prior work, Papers, Evidence, Skeptic log, Feasibility | | Saved profiles for teams |
