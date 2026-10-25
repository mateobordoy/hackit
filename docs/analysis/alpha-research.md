# Alpha: evidence-led preparation and full-cost visibility

Base: `origin/main` at `823c2cfcf35c81139e33ce7437fa4fe1e2aa057e`.
Workspace: `hackit-alpha`; branch: `alpha`. No beta changes are included.

## Research architecture

Alpha defaults to eight specialists plus Scout, an adaptive general researcher. A synthesis lead
then combines their evidence into the existing engine's strategy contract. These are distinct
prompts/roles using the configured model transport; they do not require nine model subscriptions.

| Role | Responsibility |
|---|---|
| Atlas Cost | should-cost, estimate confidence and sustainable pricing |
| Pulse Market | cost drivers, indices and price-change justification |
| Solvent Finance | liquidity, credit trends and payment economics |
| Compass Decisions | decision makers, approval limits and relationship priorities |
| Charter Contracts | notice, expiry, price obligations and clauses |
| Echo History | historical asks, settlements and negotiation observations |
| Sentinel Operations | delivery, defects, quality and demand |
| Horizon Alternatives | competing quotes, sourcing and dependence |
| Scout Discovery | independent lookups and connections across domains |

Code prepares SQL records for **every part**, not just the first one, and supplies supplier
documents from the package ID index, including release-only records. Experts work in parallel.
Scout starts alongside them. Between its turns it can see completed experts' open questions.
It can request schema, SELECT/WITH queries, literal text search and document reads by citable ID.
Requests in one response are processed together, without another model call for each request.

SQL uses a separate read-only connection, an authorizer, a row limit and a progress deadline.
Document reads reject arbitrary paths. Search covers metadata and bounded document excerpts;
it is literal search, not an embedding index or unrestricted internet browsing.

Each accepted finding cites retrieved source IDs and carries observed/inferred status, confidence,
decision use and uncertainty. Invented citations and unstructured findings are rejected. This
checks provenance, **not semantic truth**. Several agents citing one source are shared evidence,
not independent confirmation. The synthesis lead is instructed to preserve contradictions.

## Time and fallback

Default preparation budget: 40 seconds. Research gets at most 24 seconds (65% for smaller trial
budgets), up to three Scout model turns and ten non-cached tool calls. Remaining time goes to
synthesis. Late research is not awaited. Failed synthesis leaves the original numeric engine.
The alpha lead also generates the prepared argument playbook; the baseline's two separate
roleplay calls are not added on top of this budget.

Alpha does not change opening ratios, concessions, acceptance, walking away or package pricing.
Its strategy can change permitted prepared trades, argument ordering and wording as before.

`--research-mode baseline` selects main's original four-specialist preparation. Alpha cache files
are versioned under `<HACKIT_BRIEF_CACHE>/alpha-nine-v1/`; baseline files are separate. Fresh
research comparisons must use the same raw data, historical lessons and model configuration.
When testing downstream policy changes, freeze identical briefs instead.

## Full-cost sweet spot

The presentation's total-cost formula is already the engine's objective, including in its
relationship sweet-spot search. The old UI displayed the unit price attaining that objective.
Alpha exposes both the optimum unit price and its full-horizon total cost C. It does **not** invent
a replacement cost function or multiply the optimum unit price by a quantity.

`pricing.offer_summary` uses the kit's `cost_breakdown` and the same C as `buyer_cost`. Components:
purchase spend, payment financing, freight, quality, late delivery, uncovered demand, fallback
risk, supplier health, tooling, expected index movement and index risk. Rates come from the case.

Charts use complete historical packages rather than repricing every point using today's plan.
Historical costs use the current standard terms and supplier estimate, explicitly labelled.
Unknown cost is shown as unavailable. Supplier-health cost requires an estimate and its absence
is disclosed. The sweet-spot curve holds planned terms fixed: it is not a global optimum over
every permitted package and it does not use the arena's hidden best possible cost.

## Running experiments

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .

# Research only: a saved case file, real package, normal model budget, no arena session.
uv run python -m hackit2.research_trial --case P02 --case-file logs/cases/P02.json --mode alpha --data ../package
uv run python -m hackit2.research_trial --case P02 --case-file logs/cases/P02.json --mode baseline --data ../package

# Local demonstration: scripted LLM, synthetic records, local simulator, zero hosted quota/model spend.
uv run python scripts/alpha_smoke.py

# Persistent alpha UI; separate from beta and integration UI ports.
uv run hackit2 gui --offline --gui-port 8793

# Hosted practice requires the team's practice claim and Burak's authorization.
uv run hackit2 run --set practice --parallel 3 --research-mode alpha --gui --gui-port 8793 -v
```

`research_trial` writes source evidence, tool requests/results, model latencies, brief coverage and
model reliability counters under `logs/research/`. No session is opened and no lesson is appended.
Preparation comparisons measure sourced coverage, correctness, decision relevance, timeouts,
latency and model spend. End-to-end comparisons additionally measure total cost, must-haves,
score, deals and late messages across repeated runs. More agents alone are not evidence of gain.

## Validation on 25 October 2026

- Local suite: 153 passed, 2 skipped, including 19 saved case structures on the local simulator.
- Ruff lint/format and shipped JavaScript renderer checks pass.
- The labelled synthetic demo closes a deal with all nine preparation roles, discovery lookup
  telemetry and full-package cost displays. It makes zero model API calls and opens no hosted sessions.
- A real-model P02 research-only trial was attempted. The existing shared hard model budget rejected
  all calls: EUR 30.11 recorded against a EUR 30 cap. No cap was changed and no arena session opened.
  Therefore live research latency, finding quality and negotiation score gain remain unmeasured.

The user subsequently authorized a budget of 100 and ten hosted practice cases. That batch,
its preparation reliability failures, the fixes and two successful real-model research-only
checks are recorded in [the practice report](alpha-practice-20261025.md). The follow-up fixes
remove shared-connection database discovery and let synthesis use the remaining overall budget.
