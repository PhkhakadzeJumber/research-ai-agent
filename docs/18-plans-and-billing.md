# 18: Plans, Credits and Billing

Eureka is a public product for students, professors and researchers. Every run calls paid AI models, so the plans are
built around one rule: **the AI cost of what a plan includes is at most ~40% of its price**. The rest pays payment fees,
hosting and profit. Decided 2026-11-22. The numbers marked *estimate* are replaced with measured ones in the final test
round (step 11), which also checks the quality of the cheaper model mix.

## 1. The plans

| | **Free** | **Pro** |
|---|---|---|
| Price | $0 | **$10 / month**, or **$96 / year** (= $8 / month, 20% off) |
| Evaluate my idea (Mode 1, incl. a document upload) | **1 per month**, full quality | ✅ from credits |
| Find me an idea (Mode 2) | 🔒 shown, with an "Upgrade" badge | ✅ from credits |
| Research package | 🔒 | ✅ from credits |
| Credits | none | **100 every month** (reset monthly, no rollover) |
| Top-up packs | none | **50 credits for $5** (valid 12 months) |
| Library, results, Markdown export | ✅ | ✅ |

Why these choices:

- **Free gets the real evaluator, once a month.** A cheaper "quick" version on Haiku only would need its own quality
  testing and would show new users a weaker product. One full evaluation shows what Eureka is worth.
- **One paid plan.** Easy to understand; the yearly price is the discount (students included). Coupons can come later.
- **No free AI models.** Free model APIs often train on inputs (users upload unpublished research), have shared rate
  limits too low for a public site, and may forbid commercial use.

## 2. Credits

- **1 credit = $0.04 of measured AI cost.** So 100 credits cost us at most $4, which is 40% of $10. A top-up pack works
  the same way: 50 credits, at most $2 of AI cost for $5.
- **Each action has a fixed price shown before it starts** (*estimates* until step 11 measures them):

  | Action | Credits | Expected AI cost | So 100 credits buy about |
  |---|---|---|---|
  | Evaluate an idea | **25** | ~$1.00 | 4 evaluations |
  | Find me an idea | **60** | ~$2.40 | 1 discovery + 1 evaluation |
  | Research package | **5** | ~$0.20 | |

- **Reserve, then settle.** Starting an action reserves its price. At the end the user is charged
  `min(price, ceil(actual cost / $0.04))`, and the rest goes back. The user never pays more than the price shown.
  An action can take several runs (an evaluation: the first reading, then the evaluation after the clarification,
  then any resumes): each later run reserves what is left of the price, so the action as a whole never costs more.
  If nothing is left (a run that reached its cost cap), a resume reserves a fifth of the price, shown before resuming.
  Built in step P2: `src/api/billing.py`.
- **Stops and failures.** If the user stops a run, they pay for what was used (same formula). If a run fails because of us
  (a crash, a provider outage), everything is refunded. Resuming a paused run reserves the remaining price again.
- **Per-run safety cap.** A run's AI budget is 1.5× its price in dollars (an evaluation: $1.50), so an unusual run can
  overrun its price a little (we absorb it) but never by much. This replaces the dollar budget users set today.
- **Not enough credits.** The start button says so and offers a top-up or the yearly plan. Nothing starts half-funded.

## 3. Protecting the free plan

- **Email verification** before the first run, so one person can't farm free accounts.
- **A global monthly cap on free usage** (`EUREKA_FREE_MONTHLY_BUDGET_USD`). When it's reached, free evaluations wait
  until next month (the page says so and offers Pro). This puts a hard ceiling on what free users can cost.
- One free evaluation per account per calendar month (Tbilisi time, as the profile's monthly spend is today).

## 4. Unit economics (*estimates*)

| Per Pro user per month | Worst case (all credits used) | Typical (half used) |
|---|---|---|
| Revenue | $10.00 | $10.00 |
| Payment fees (merchant of record, ~5% + $0.50) | −$1.00 | −$1.00 |
| AI cost | −$4.00 | −$2.00 |
| **Left for hosting and profit** | **$5.00** | **$7.00** |

Hosting (API, database, frontend) is roughly a fixed $30–60 a month at launch, so about **10 Pro users break even**;
the free cap (§3) is the other fixed cost and is set by us.

## 5. The model mix (cost reduction, step 1 of the plan)

| Agents | Model | Why |
|---|---|---|
| Paper Reader, relevance screening, opportunity mining | Haiku 5.5 | High volume, simple extraction; code verifies every quote word for word |
| Everything else (Idea Analyst, Scout, Novelty, Skeptic, Value & feasibility, Proposal) | Sonnet 5.5 | Reasoning-heavy; half the price of Opus |

This is configuration only (`.env`, see `.env.example`). Measured on all-Opus: Mode 1 cost $3.51; the mix is expected to
bring it near $1. Prompt caching and lower effort where quality holds come after the measurement.

## 6. What users see

- No dollar amounts or model names on user pages: plans, credits and "≈ 25 credits" estimates instead. The AI provider
  is still named in the document-upload consent text (users must know where their document goes).
- **Pricing page** (`/pricing`, public): the two plans, the credit table, FAQ.
- **Profile → Plan & billing**: plan and renewal date, credits left (monthly and top-up), a credit history
  (reserved / charged / refunded per investigation), "Manage billing" (the provider's portal), top-up.
- **Start page**: the Mode 2 card shows "🔒 Pro" for free users; each start button shows its credit price; free users
  see "1 free evaluation left this month".
- **Nav chip**: credits left (Pro) or "Free · 1 evaluation left".
- Admin (us) still sees dollar costs per run, through the scripts and the database.

## 7. Payments

A **merchant of record** (Paddle or Lemon Squeezy) is the expected choice: it sells on our behalf, handles VAT and sales
tax worldwide and pays out to us, for about 5% + $0.50 per sale. Stripe may not accept a business based in Georgia;
this and each provider's current terms are checked before step P5 is built. Until then, Pro can be granted by an admin
script, so everything else is built and tested without payments.

## 8. Build steps

| Step | What | Needs approval |
|---|---|---|
| P1 | Model mix in `.env` | — (the user edits `.env`) |
| P2 ✅ | Backend: plan fields on users, a credits ledger (grant / reserve / charge / refund), entitlement checks on start, resume and package, the free cap, `scripts/users.py grant-pro` / `grant-credits` | Migration, response shapes |
| P3 | Email verification and password reset | An email-sending service (dependency, env vars) |
| P4 ✅ | UI: Pricing, Plan & billing, Pro badges, credit chip, credit prices instead of dollars | — (design in Figma first) |
| P5 | Payments: checkout, billing portal, webhooks that grant monthly credits and top-ups | Provider choice, dependency, env vars |
| P6 | Terms of service, privacy policy, refund policy | — |

Then the single test round (step 11) and deployment (step 12).
