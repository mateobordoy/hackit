# Hack It
This repository contains our autonomous AI buyer agent developed for the Tacto Challenge Industrial Agents Hackathon 2026. The core problem is building an agent that negotiates industrial procurement contracts via email with zero human intervention.

Operating within the Tacto Negotiation Arena track, our solution is evaluated on a multidimensional cost function. It must mathematically optimize unit price, payment terms, lead times, and quality against multi-agent supplier departments. Leveraging 33,000 rows of market data, our agent generates strategic arguments to capture the maximum possible savings score across the five-wave Live Final.

## hackit2: the buyer agent

An autonomous buyer for Hollerau Pumpenwerke. For each case it negotiates by e-mail with the
supplier bots and closes the term sheet with the lowest total cost C, or walks away when the
alternative is cheaper.

### Principle

Code owns every binding number: prices, terms, accept and walk-away are computed with the kit's
`total_cost` and `must_haves_ok`. Language models only advise (clamped to fixed lists) and write one
paragraph per e-mail; a paragraph with any digit, data ID or leak is dropped for a template.

### How a case runs

1. **Prepare** (before the session opens, ≤ 40 s): four specialist models study slices of the data
   package per supplier; a lead researcher proposes up to two trades, the argument order and the
   tone; a sparring supplier and buyer rehearse the arguments into a playbook (`hackit2/prep/`).
2. **Negotiate** (one message at a time, the longest-waiting thread first, within each 60 s clock):
   the reply reader classifies mood and tactic; the engine plans non-price terms by C at the
   supplier's own valuation, opens below the quote and closes a share of the gap each round,
   citing verified evidence (`hackit2/negotiation/`, `hackit2/evidence.py`, `hackit2/pricing.py`).
3. **Close**: echo back a cheaper sheet the supplier offered, then accept the cheapest acceptable
   standing sheet or walk away. Scored cases leave a lesson for similar later cases (`hackit2/lessons.py`).

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
uv run hackit2 run --set final --parallel 10 -v 2>&1 | tee logs/final.log   # Live Final
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```

Environment variables (names only): `ARENA_URL`, `ARENA_API_KEY`, `ARENA_DATA`, `LLM_PROVIDER`,
`LLM_MODEL`, `LLM_EFFORT`, `ANTHROPIC_API_KEY`, `LLM_BASE_URL`, `LLM_API_KEY`, `HACKIT_BUDGET_EUR`,
`HACKIT_SPEND_FILE`.

### Credits

See `NOTICE`.
