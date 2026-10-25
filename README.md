# Hollerau buyer: a negotiator that models each supplier

An autonomous buyer for the [Tacto Negotiation Arena](https://github.com/tacto-playground/negotiation-arena-kit).
It negotiates by e-mail with supplier bots and closes the term sheet with the lowest total cost C,
or walks away when the alternative is cheaper.

**The idea.** Previous agents (including ProcureCabana, the jury winner of the Munich 2026 final)
follow a recipe: open at X % of the quote, concede 30 % of the gap per round, slow down when a
model says "hold". This agent instead treats every supplier as a set of hypotheses - where its
floor is, how fast it steps down, how much patience it has - narrows them with every reply, and
chooses every next price by simulating each candidate against the hypotheses still alive, scoring
the outcomes in euros with the case's own cost function. The suppliers are mechanical (they step
toward a hidden floor, concede only after a real move, agree as soon as our sheet reaches their
next counter, final after a token move or once patience runs out), which is exactly the kind of
opponent that inference plus planning beats.

**Code owns every number.** Prices, terms, claims, accept and walk-away are computed. An optional
model may add one polite paragraph per e-mail; anything with a digit, a data ID or a leak is dropped.

## Run it

Python 3.12.

```sh
python -m venv .venv && .venv\Scripts\activate        # Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
copy .env.example .env                                  # fill in ARENA_API_KEY (never commit .env)
buyer data                                              # data package (+ Sunday release once public)

buyer sandbox -v                                        # D01 + D02: free, no practice quota
buyer practice --repeat 2 -v                            # every hosted practice case twice (20 sessions)
buyer report                                            # mean/best per case vs ProcureCabana
buyer final                                             # the Live Final: start before 13:30, leave running
```

`buyer play case:P08 --repeat 2` plays a single case. Every session writes its full decision trace
to `logs/runs/<case>_<session>.json`; `python scripts/trace.py logs/runs P08` prints it readably
(every price, the planner's table of candidates with expected C, the belief about the floor).

## Watch it (control room)

```sh
buyer gui                                               # http://127.0.0.1:8790, any run of this or a sibling checkout
buyer practice --gui -v                                 # the agent and the UI in one process
python scripts/sim_demo.py X01 X02 S01                  # local arena-sim runs, no arena, no quota
```

Every case appends its decisions, e-mails, model calls and result to `logs/live/case_<id>.jsonl`
(`buyer/telemetry.py`, passive; `--no-live` turns it off). The page shows the system map with the data
that moved between the modules, the e-mail thread, the belief about each supplier's floor (p10-p90)
with the planner's candidate table behind every price, the preparation, the model calls, a decision
feed, Scores against the ProcureCabana reference and the run History. Contract: `docs/liveview-contract.md`.
In `buyer final --gui` the UI makes no arena call at all.

## Tests

```sh
pytest -q            # 37 tests, about 75 s (one real-clock Live Final rehearsal); no arena, no quota
ruff check . && ruff format --check .
```

- `test_sim_end_to_end.py`: the kit's `arena-sim` (the real API contract) on its 3 cases plus 4
  practice-like cases built on the real data package (P01, P05, P08, P11 suppliers).
- `test_flow_guards.py`: hosted-like fake bots (`tests/fakebot.py`) check the guards: never push
  after a final offer, no token moves, no walk-away while an acceptable sheet stands, must-haves
  kept, timeouts rolled back, RFQ closes the cheapest acceptable bidder, every sheet valid.
- `test_final_rehearsal.py`: `buyer final`'s wave loop on the sim's Live Final rehearsal.
- unit tests for the economics, belief, planner, evidence, writer and supplier profile.

## How a case runs

1. **Prepare** (per supplier, no model, < 1 s): standard terms from its quote PDF; its profile from
   the data (owner type, persona traits in notes, letters, news and quote remarks, its cost of
   money); its full cost from the should-cost table; the non-price terms that cost us least at
   equal value to the supplier (`economics.plan_terms`); the evidence ranked by how often each kind
   actually moved practice suppliers; a prior over its floor and patience by case type.
2. **Negotiate**: open at a proven anchor with the 3 strongest verified claims; then each reply
   updates the belief (`belief.py`) and the planner (`planner.py`) picks the next price. In RFQs
   the cheapest rival offer is cited to the others every round and dominated bidders stop.
3. **Close**: accept the cheapest standing sheet that keeps every must-have and beats the
   alternative, else walk away; in the final, at the latest 90 s before the wave's cap.

## Code map

| Path | Role |
|---|---|
| `buyer/config.py` | every tunable number, each with the evidence it rests on |
| `buyer/economics.py` | C, the accept gate, the supplier's valuation, term planning, the alternative's price ceiling |
| `buyer/supplier.py` | profile, traits, cost of money (learned online from its priced alternatives), floor priors |
| `buyer/evidence.py` | data arguments the arena verifies, ranked by measured effect |
| `buyer/belief.py` | the supplier as particles: floor, step, patience, token-move threshold |
| `buyer/planner.py` | candidate prices simulated against the belief, scored in euros |
| `buyer/policy.py` | the per-thread negotiation and every guard |
| `buyer/runner.py` | one message at a time, clocks, keepalives, close; the Live Final waves |
| `buyer/writer.py`, `buyer/llm.py` | e-mails (templates; optional model paragraph, guarded) |
| `buyer/records.py`, `scripts/trace.py` | run logs, the report, a readable trace |

## Lessons taken from ProcureCabana (and what changed)

Kept, because their logs proved them: anchors by category goal; never push after `final_offer`;
mirror a cheaper sheet the supplier offered itself; one last sheet at the ceiling only when what
stands is unacceptable; a tolerant quote-PDF reader; bound the full-cost estimate by the
supplier's own offers; prune dominated bidders; keepalives; resume crashed final cases.

Changed:
- **Pace** comes from the planner and the belief, not from a fixed 30 % with a "hold" brake
  (the brake made suppliers final a round early). Token moves (< 0.8 %) are impossible.
- **Evidence** is ranked by its measured effect: forecasts (84/97 taken into account), rival
  quotes, falling indices; our own order prices (0/100) are never cited.
- **Cost of money** is learned from the supplier's priced alternatives and from which of our two
  round-1 packages it picks, instead of a fixed table by owner type.
- A term the supplier refuses twice (payment, lead time, ...) is conceded, never the index clause
  or quality terms.
- Their stall guard never fired (it waited for `from == "supplier"`; the arena sends
  `sales@<id>`): nothing here depends on the sender's name.
