# Hack It
This repository contains our autonomous AI buyer agent developed for the Tacto Challenge Industrial Agents Hackathon 2026. The core problem is building an agent that negotiates industrial procurement contracts via email with zero human intervention.

Operating within the Tacto Negotiation Arena track, our solution is evaluated on a multidimensional cost function. It must mathematically optimize unit price, payment terms, lead times, and quality against multi-agent supplier departments. Leveraging 33,000 rows of market data, our agent generates strategic arguments to capture the maximum possible savings score across the five-wave Live Final.

## hackit2: the buyer agent

An autonomous buyer for Hollerau Pumpenwerke. For each case it negotiates by e-mail with the
supplier bots and closes the term sheet with the lowest total cost C, or walks away when the
alternative is cheaper.

### Approach

1. **Code owns every number (`hackit2/pricing.py`, `hackit2/negotiation/`):** All commercial decisions—unit prices, non-price term packages, index-clause trades, convex relationship margin floors (`relationship_sweet_spot`), alternative ceilings (`price_ceiling`), echoes, accepts, and walk-aways—are computed deterministically with `arena_kit.cost.total_cost` and `must_haves_ok`. Language models only advise (clamped to fixed lists) and write one paragraph per e-mail; any paragraph with a digit, data ID, or cross-supplier leak is dropped (`model.UNSAFE_TEXT`) for a safe template.
2. **Prepare (`hackit2/prep/`, ≤ 40 s before open):** Four specialist models study slices of the data package per supplier; a lead researcher proposes up to two trades, the argument order, and the tone; a sparring supplier and buyer rehearse arguments into an objection playbook.
3. **Negotiate (`hackit2/negotiation/`, `hackit2/evidence.py`):** One message at a time (longest-waiting thread first, within each 60 s clock). The reply reader classifies mood, tactic, and requests; the engine plans non-price terms by $C$ at the supplier's own valuation, offers cost-neutral prepared alternatives in Round 1 and cost-reducing need alternatives in later rounds, and cites verified evidence (`po:`, `idx:`, `sc:`, `fc:`).
4. **Close (`hackit2/negotiation/closing.py`, `hackit2/session.py`):** Echo back a cheaper sheet the supplier offered, insure acceptable standing sheets before round/timeout limits expire, and accept the lowest-$C$ acceptable sheet or walk away. Scored cases leave a lesson for similar later cases (`hackit2/lessons.py`).

### Layout

| Path | Role |
|---|---|
| `hackit2/settings.py` | every tunable constant |
| `hackit2/pricing.py` | C, supplier worth, term planning, price ceilings |
| `hackit2/evidence.py` | citable arguments in the format the arena verifies |
| `hackit2/supplier_profile.py` | ownership, traits, cost of money |
| `hackit2/negotiation/` | per-thread state, concession step, packages, closing, engine |
| `hackit2/prep/` | preparation team and sparring round |
| `hackit2/reply_reader.py`, `hackit2/mailer.py`, `hackit2/transcript.py` | reading replies, writing e-mails |
| `hackit2/model.py` | model transports (Anthropic with spend cap, OpenAI-compatible), timeouts, guards |
| `hackit2/session.py` | the case loop, batches and the Live Final loop |
| `hackit2/cli.py` | command line |

### Setup and use

```sh
uv sync                                        # Python 3.12
cp .env.example .env                           # fill in ARENA_API_KEY and ANTHROPIC_API_KEY
uv run arena data                              # data package into data/
uv run hackit2 play case:D01 -v                # sandbox, free
uv run hackit2 play case:P01 -v                # practice (uses team quota)
uv run hackit2 run --set practice --parallel 10
uv run hackit2 run --set final --no-gui --parallel 10 -v 2>&1 | tee logs/final.log   # Live Final
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```

Live watch UI (reads `logs/live/`, never contacts the arena). The agent runs the same way with or
without it:

```sh
uv run hackit2 run --set practice                   # default: no UI, live files written (attach a UI any time)
uv run hackit2 run --set practice --gui             # agent + watch UI in one process, http://127.0.0.1:8790
uv run hackit2 gui                                  # the watch UI alone, for runs started elsewhere
uv run hackit2 run --set final --no-gui             # headless: no UI, no live files (or HACKIT_NO_GUI=1)
```

It shows, per case, the module map with the code running right now and the data flowing between
modules, every LLM call (role, prompt Python sends, answer, latency, whether it was used), the
preparation team's findings, strategy and sparring playbook, the e-mail thread with term sheets and
claim checks, the price paths and the full decision trace; finished cases can be replayed.

Environment variables (names only): `ARENA_URL`, `ARENA_API_KEY`, `ARENA_DATA`, `ARENA_RELEASE`,
`LLM_PROVIDER`, `LLM_MODEL`, `LLM_EFFORT`, `ANTHROPIC_API_KEY`, `LLM_BASE_URL`, `LLM_API_KEY`,
`HACKIT_BUDGET_EUR`, `HACKIT_SPEND_FILE`, `HACKIT_NO_GUI`, `HACKIT_BRIEF_CACHE`,
`HACKIT_REFERENCE_FILE`.

Submitted commit: `<HASH>` (see `docs/submission.md`).

### Licences and third-party notices

See `NOTICE`.
