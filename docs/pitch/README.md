# Team Hack It — 5-Minute Top-5 Pitch Deck & Live Demo Script (Task T8)

- **Team:** Hack It (Burak, Andrey, Jose, Mateo)
- **Track:** Industrial Agents Hackathon 2026 — Tacto Negotiation Arena
- **Format:** 5 minutes total (~3m 15s slides + ~1m 45s live UI demo)

---

## Part 1: 5-Minute Pitch Deck Outline (6 Slides)

### Slide 1 — The Core Thesis: *Code Owns Every Number, Models Own the Persuasion* (0:00 – 0:40)
- **The failure mode of naive LLM negotiators:** When an LLM chooses prices, payment terms, or walk-away decisions, it hallucinates numbers, violates hard must-haves (scoring `0`), or pushes a supplier past its `final_offer`.
- **Our architecture rule:**
  - **Deterministic Python arithmetic (`hackit2/pricing.py` & `hackit2/negotiation/`) owns 100% of commercial decisions:** every unit price, non-price package, index-clause valuation, relationship margin floor (`relationship_sweet_spot`), price ceiling (`fallback_cost`), echo, accept, and walk-away.
  - **LLMs advise within clamped schemas and write one persuasive paragraph per e-mail:** Any LLM output containing raw digits or database IDs is rejected by a strict regex text guard (`model.UNSAFE_TEXT`), while verified claims (`po:`, `idx:`, `sc:`, `fc:`) and exact term sheets are assembled deterministically by code (`evidence.py`, `mailer.py`).

### Slide 2 — End-to-End Architecture (`hackit2`) (0:40 – 1:30)
```
Data Package (SQLite + PDFs + Case Facts)
   │
   ├─► 1. Deterministic Valuation & Evidence Bank (pricing.py, supplier_profile.py, evidence.py)
   │      • Should-cost (own-plant vs generic), delivered PPM, cash-need interest rate, 12m index trends
   │      • Exact arena-verifiable claim sentences (order history, scorecards, indices, forecasts, rival quotes)
   │
   ├─► 2. Pre-Round Preparation Team & Sparring (prep/briefing.py, prep/sparring.py, lessons.jsonl)
   │      • 4 specialist lenses (Economics, Persona, History, Leverage) + Lead Researcher + Seller Sparring
   │      • Outputs clamped trade candidates, argument order, and objection playbook (0 numbers touched)
   │
   ├─► 3. Multi-Thread Session & Negotiation Engine (session.py, negotiation/{engine,concession,packages,closing}.py)
   │      • Parallel RFQ competition: cross-supplier competitive leverage without leaking rival names
   │      • Cost-neutral prepared alternatives (Round 1) & cost-reducing need alternatives (Round 2+)
   │      • Buyer-cost-guarded index-clause trades & convex relationship sweet-spot search
   │      • Hard safety gates: never push after final_offer/agreed; never walk away while an acceptable sheet stands
   │
   └─► 4. Reply Reader & Guarded Mailer (reply_reader.py, mailer.py, model.py)
          • Extracts supplier mood, tactic, and requests (payment, volume)
          • Renders natural prose around deterministic term sheets and verified data citations
```

### Slide 3 — Multi-Term Package Engineering & Relationship Economics (1:30 – 2:15)
- **Exact total-cost (`C`) optimization (`pricing.py`):**
  - Every allowed combination of `payment_days`, `incoterm`, `lead_time_days`, `contract_months`, `volume_per_year`, `ppm_commitment`, `chargeback`, and `tooling_eur` is repriced at the supplier's own valuation (`supplier_worth`) and ranked by buyer total cost `C` (`arena_kit.cost.total_cost`).
- **Three package engines (`negotiation/packages.py`):**
  1. **Index-Clause Trade (`offer_index_clause`):** Adopts an index clause when the supplier's 12-month driver rose faster than the forward path—guarded so a clause is only adopted when `c_with < c_without - margin` after relationship sweet-spot clamping.
  2. **Cost-Neutral Prepared Alternatives (`prepared_alternatives`):** Bisects unit price so each alternative sheet has the *exact same buyer cost `C`* as our main sheet; whichever sheet the supplier picks, we lose nothing and discover its true preference.
  3. **Need-Driven Replans (`need_alternatives` & `adopt_cheaper_package`):** Dynamically answers stated or inferred supplier needs (`cash_need`, `spare_capacity`, `capacity_full`) only when the complete package strictly reduces buyer `C`.
- **Convex Relationship Sweet Spot (`relationship_sweet_spot`):**
  - On `stabilise` and `secure_supply` cases, pushing a near-cost supplier below its sustainable margin increases `C` via the relationship penalty. We locate `C`'s convex minimum over `[0.85 * full_cost, quote_at_plan]` and settle immediately when the supplier counters at or below our sweet spot.

### Slide 4 — Reliability Under Live Final Constraints (2:15 – 2:45)
- **Built for 5 waves x 8 minutes, zero human in the loop (`session.py`):**
  - **Per-supplier clock budgeting & keepalives:** Tracks supplier timeout clocks, deducts reader latency from the writer's budget, and falls back deterministically (`--no-llm` template path) if a model call times out or hits the shared EUR spend cap (`HACKIT_SPEND_FILE`).
  - **Crash-safe thread resumption (`resume_thread`):** Reconstructs `our_prices`, `their_prices`, `gap_history`, and shown alternatives from arena event history if a process restarts mid-wave.
  - **Zero-disqualification safety:** `must_haves_ok` and `acceptable_cost < fallback_cost` are checked on every outgoing and accepted sheet.

### Slide 5 — Live Telemetry & Watch UI Demo (2:45 – 4:30)
- *(Transition directly to live browser demo — see Part 2 below.)*

### Slide 6 — Results & Takeaways (4:30 – 5:00)
- **Practice Benchmark Performance (10 hosted practice cases):**
  - **77.6 – 78.5** mean score across all 10 hosted practice cases (`logs/compare/digest.md`), **100% deal closure**, **100% must-haves satisfied**, zero disqualifications.
  - Deterministic Round 1 openings across runs; targeted fixes verified with unit + simulator tests (`91+` tests green).
- *[Placeholder for Burak: add any preferred remarks on team workflow / codebase progression.]*

---

## Part 2: Live Demo Script (1m 45s)

### Pre-Demo Terminal Setup (before going on stage)
```sh
# 1. Start the hackit2 Watch UI on port 8790 (reads logs/live/, logs/runs/, logs/sessions/):
uv run hackit2 gui --port 8790

# 2. (Optional, if running a live sandbox case during the pitch):
uv run hackit2 play case:D02 --gui --gui-port 8790 -v

# 3. (Optional dev-process view, outside the hackathon repo):
# http://127.0.0.1:8795 (agents-hub/dashboard.py)
```

### Step-by-Step Demo Walkthrough (`http://127.0.0.1:8790`)

1. **Scores View (`0:00 – 0:35` of demo):**
   - Click the **Scores** tab in the Watch UI (`http://127.0.0.1:8790`).
   - Point out the side-by-side comparison across all practice cases (`P01`–`P18`): **Latest Run vs. Team Best vs. Reference**, along with `C_open`, `C_deal`, `C_best`, and per-case score bars.
   - Highlight consistency: 100% must-have compliance and strong multi-supplier RFQ compression (e.g., `P01`, `P05`, `P14`, `P15`).

2. **Case Deep-Dive & Price Trajectory SVG (`0:35 – 1:15` of demo):**
   - Select a multi-supplier RFQ case (e.g., `D02` or `P05`) or a relationship case (`P02`).
   - Show the **per-supplier thread cards**:
     - **Price convergence chart:** Our opening and step path (`our_prices`) vs. supplier counters (`their_prices`), plus the horizontal **quote** and **sweet spot** reference lines.
     - **Preparation Brief & Playbook panel:** Show how the 4-specialist prep team + sparring partner generated structured findings, trade candidates, and objection answers before Round 1.
     - **Live Function Trace & Decision Log:** Point out traced calls (`pricing.cheapest_terms`, `packages.prepared_alternatives`, `packages.offer_index_clause`, `closing.wrap_up`) proving that every number on every term sheet comes from `pricing.py`.

3. **Optional 20-Second Engineering Workflow Peek (`1:15 – 1:45` of demo, at Burak's discretion):**
   - Switch tab to `http://127.0.0.1:8795` (*Multi-Agent Hub Control Center*, strictly a development tool outside the repository).
   - Briefly show how parallel coding agents (`opencode`, `claude`, `codex`, `agy`) coordinated via file-level lock claims (`BOARD.md`), inter-agent mailboxes, and automated test gates without stepping on each other's files.
