# Team Hack It — Final Submission (Tacto Negotiation Arena 2026)

- **Team:** Hack It (Burak, Andrey, Jose, Mateo)
- **Track:** Tacto Negotiation Arena — Industrial Agents Hackathon 2026 (Munich)
- **Submitted Commit Hash:** `<HASH>`
- **Branch:** `main` (fast-forwarded from `burak`)

---

## 1. Approach

`hackit2` is an autonomous procurement buyer agent for Hollerau Pumpenwerke that negotiates industrial supply contracts via e-mail against Tacto's multi-agent supplier bots with zero human intervention.

1. **Code Owns Every Number (`hackit2/pricing.py`, `hackit2/negotiation/`):**
   - Every commercial decision—unit prices, non-price term packages, index-clause trades, convex relationship margin floors (`relationship_sweet_spot`), alternative ceilings (`price_ceiling`), echoes, accepts, and walk-aways—is computed deterministically using `arena_kit.cost.total_cost` and `must_haves_ok`.
   - Language models never set a price, term, or closing action. Model prose is filtered through a strict regex safety gate (`model.UNSAFE_TEXT`) that drops any paragraph containing digits, data IDs, or cross-supplier leaks in favor of a safe deterministic template.
2. **Pre-Round Specialist Preparation & Sparring (`hackit2/prep/`, `hackit2/lessons.py`):**
   - Before a session opens ($\le 40\text{ s}$ bounded budget), four parallel specialist prompts (Economics, Persona, History, Leverage) analyze the SQLite/PDF data package and past cross-case lessons (`lessons.jsonl`). A Lead Researcher and Seller/Buyer Sparring loop distill up to two non-price trade candidates, an argument priority order, and an objection playbook.
3. **Multi-Package Negotiation & RFQ Competition (`hackit2/negotiation/engine.py`, `packages.py`, `closing.py`):**
   - **Term Planning & Index-Clause Trades:** Plans non-price terms by minimizing buyer total cost $C$ at the supplier's estimated valuation (`supplier_worth`), and trades index clauses when the supplier's 12-month driver rose faster than the forward path while respecting the relationship sweet-spot floor.
   - **Cost-Neutral & Cost-Reducing Alternatives:** Attaches cost-neutral prepared alternatives in Round 1 (`prepared_alternatives`) to discover supplier preferences at zero buyer loss, and dynamically offers cost-reducing need alternatives (`need_alternatives`) or switches to cheaper supplier packages (`adopt_cheaper_package`) in later rounds.
   - **Live Final Safety & RFQ Insurance:** Tracks per-supplier 60 s clocks, sends keepalives when needed, echoes back cheaper supplier alternatives before closing, insures acceptable standing sheets before round/timeout limits expire, and resumes cleanly from arena history if a process restarts.

---

## 2. Environment Variable Names (`.env`, names only)

Set in `.env` (copied from `.env.example`; never committed):
- `ARENA_URL`
- `ARENA_API_KEY`
- `ARENA_DATA`
- `ARENA_RELEASE` *(optional override; defaults to `release-sunday` inside or next to `ARENA_DATA`)*
- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_EFFORT`
- `ANTHROPIC_API_KEY`
- `LLM_BASE_URL` *(optional, for OpenAI-compatible provider)*
- `LLM_API_KEY` *(optional, for OpenAI-compatible provider)*
- `HACKIT_BUDGET_EUR`
- `HACKIT_SPEND_FILE` *(optional, shared EUR spend ledger path)*
- `HACKIT_NO_GUI` *(optional, headless toggle)*
- `HACKIT_BRIEF_CACHE` *(optional, JSON path for persisting preparation briefs across waves/resumes)*
- `HACKIT_REFERENCE_FILE` *(optional, reference run manifest/digest for Watch UI)*

---

## 3. Sunday 08:00 CET Data Release & Live Final Run Command

```sh
# 1. Install dependencies & verify test suite
uv sync
uv run pytest -q && uv run ruff check . && uv run ruff format --check .

# 2. Pull the Sunday 08:00 CET data release into ARENA_DATA
uv run arena data

# 3. Run the Live Final (headless, 5 waves, up to 10 cases in parallel, logged to logs/final.log)
mkdir -p logs
uv run hackit2 run --set final --no-gui --parallel 10 -v 2>&1 | tee logs/final.log
```
