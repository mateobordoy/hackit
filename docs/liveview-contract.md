# HackIt Buyer Live: watch UI contract (`buyer gui`, `buyer/liveview.py` + `buyer/liveview.html`)

The agent appends JSON lines to `logs/live/case_<P01>.jsonl` while a case runs (`buyer/telemetry.py`).
`buyer/liveview.py` serves the page and these endpoints (standard library only, http://127.0.0.1:8790).

## GET /api/cases
`[{case, source, case_id, title, type, goal, set, session, status: "live"|"done", last, started,
   suppliers: [sid], deal, supplier_id, score, c_deal, c_alt, end_reason}]` newest/live first.
`case` is the key for /api/events ("P05", or "P05@hackit-codex" for a sibling checkout).

## GET /api/events?case=P05&start=N
`{events: [...], next: M, run_start: epoch}`; poll with start=next. Every event has `at` (epoch s),
`event`, and `transfers` (see below). Event kinds:

- `case_start`: `{case_id, title, type, goal, set, session, suppliers: [sid], c_alt, need_per_year,
  horizon_years, current_unit_price, negotiable: [field], must_haves: {...}}`
- `note`: one decision of the negotiator, `{supplier, what, t, ...}`. Important `what` values and fields:
  - `setup`: `quote` (its terms), `std`, `signals`, `owner`, `rate`, `full_cost`, `terms` (our planned
    terms), `q0` (its opening price at our terms), `anchor`, `target`, `prior` [mean, sd] (floor as share of q0), `claims` [kind]
  - `reader`: `where` (setup|reply), `signals`, `asks`, `stance`, `evidence` [{label, quote}], `summary`
  - `memory`: `n`, `observed`, `shift`, `prior`
  - `reply`: `status` (open|final_offer|agreed|...), `theirs` (its price at our terms), `gap_sum`,
    `belief` {floor_p10, floor_p50, floor_p90 (shares of q0), patience, alpha, ess}, `asks`, `signals`, `claim_results`
  - `planner`: `price` (chosen, EUR), `expected` (expected C EUR), `table` [[price EUR, expected C, P(walk), expected rounds]], `belief`
  - `planner_accept`: `expected`, `accept`, `table`
  - `send`: `price`, `theirs`, `sheets` (count), `claims` [kind], `mirror`, `model` (bool: LLM paragraph used)
  - others (show as a line in the decision feed): `hold_after_deep_drop`, `probe_sheet`, `need_sheet`,
    `rate_learned`, `switch_terms`, `clause_trade`, `dominated`, `keepalive`, `rollback`, `refused`,
    `at_ceiling`, `at_relationship_target`, `no_progress`, `no_real_step`, `mirror`, `last_sheet`,
    `fallback`, `restore`, `accept` (supplier, c, offer), `walk_away` (c_alt)
- `message_out`: `{supplier, subject, body, term_sheets: [{terms}], refs: [id], action?}`
- `message_in`: `{supplier, from, status, body, term_sheets: [{term_sheet_id, terms}], alternatives,
  claim_results, attachments: [name]}`
- `timeout`: `{supplier, detail}` (our message came too late; the arena answered)
- `llm`: `{supplier, role: "reader"|"writer", prompt (excerpt), answer (text or labels), seconds,
  outcome: "ok"|"none"}`
- `end`: `{result: {deal, supplier_id, terms, c_open, c_deal, c_alt, c_best, score, end_reason}}`

`transfers`: `[{id, from, to, label, at, supplier, request, response}]` - the data that moved between two
modules at this event. Modules (the system map):

| id | label | role |
|---|---|---|
| arena | Arena | supplier bots, e-mail API |
| runner | Runner | one message at a time, clocks, keepalives, close |
| supplier | Supplier profile | owner type, signals, cost of money, should-cost |
| reader | Reader agent (LLM) | labels the supplier's situation with verbatim quotes |
| evidence | Evidence bank | data claims ranked by measured effect |
| economics | Economics | total cost C, accept gate, term planning, ceiling |
| memory | Memory | where suppliers stopped in earlier cases: floor prior shift |
| belief | Belief (400 particles) | floor, step, patience - narrowed by every reply |
| planner | Planner | simulates candidate prices against the belief, scores in EUR |
| policy | Negotiator / guards | per-thread decisions and the guards that never bend |
| writer | Writer (template + LLM) | e-mail text; code owns every number |
| records | Records | run log, report |

Edges: arena-runner, runner-policy, supplier-policy, reader-policy, evidence-policy, economics-policy,
memory-belief, belief-planner, planner-policy, economics-planner, policy-writer, writer-runner, runner-records, runner-memory.

## GET /api/scores
`{rows: [{case, latest, previous, change, best, mean, n, team_best, reference_best, reference_batch,
  runs: [score...] oldest first}], totals: {mean, best, reference_batch, reference_best}, team: {at, error, offline}}`

## GET /api/history
`{runs: [{session, case_id, title, set, type, started, score, deal, source, has_live}]}`

## GET /api/run?session=ID
Same shape as /api/events for a finished run, built from `logs/live` if present else from
`logs/runs/<case>_<session>.json` (notes only, no e-mails).

## GET /api/showcase[?session=ID or bundled name]
The jury Showcase storyboard: see AGENT_SHOWCASE.md (schema) and docs/showcase-sample.json (a real one).
