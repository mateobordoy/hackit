# Weak Practice Cases Analysis: P08, P18, P11, P02 (Task T9)

This analysis compares our `hackit2` practice runs (`logs/runs/*.json` and `logs/sessions/practice/*.json`) against our best historical reference sessions (`logs/compare/digest.md` and `logs/compare/chats.html`) to isolate the exact deterministic and LLM-mediated levers where score was lost, and ranks concrete code-owned fixes by expected score/EUR gain vs. risk.

---

## 1. Per-Case Root Cause Breakdown

### P02 — `sup:70510` (Antriebstechnik Nordmark AG, `stabilise`, 1-supplier relationship case)
- **Sessions compared:** `P02_2cd93357d9b549f0` (`0.7347`), `P02_64868073b4705303` (`0.7347`), `P02_b383447873baa8d2` (`0.7347`).
- **Cost benchmark:** `C_open = €2,282,867.49`, `C_best = €2,235,514.05` (`C_open - C_best = €47,353.44`), `C_deal = €2,248,075.11` (gap to `C_best`: **`€12,561.06`** = `-0.2653` score).
- **What happened (`offer:70510-1`, Round 1):**
  - `P02` is a `stabilise` (`RELATIONSHIP_GOALS`) case where our own-plant `full_cost` estimate (`€370.64`) is within 3% of the quote (`€370.96`), setting `near_cost = True` (`patience = 0.14`).
  - `open_thread` (`hackit2/negotiation/engine.py:130-156`) plans `payment_days = 90` (`quote_at_plan = €374.06`) and clamps both `thread.price` and `thread.floor` at `relationship_sweet_spot = €361.67`.
  - Next, `offer_index_clause` (`hackit2/negotiation/packages.py:130-165`) evaluates `CU-NOTATION (material_share=0.2, deadband_pct=5.0%)`. Because `CU-NOTATION` rose over the last 12 months, `worth > cost * 1.2` is `True` (`packages.py:146`), so it adds `CU-NOTATION` directly into `thread.plan["index_clause"]` and attempts to discount `thread.price` on line 157:
    ```python
    thread.price = round(max(thread.price - worth * 0.6, floor), 2)
    ```
  - **Root cause (clamped discount + `supplier_value` mismatch):** In `arena_kit.cost.supplier_value` (`arena_kit/cost.py:80`), *"Contract length and an index clause are security, not cash: they do not count here."* Thus `relationship_sweet_spot` remains `€361.67` (`== floor`), and `max(361.67 - worth * 0.6, 361.67)` yields **`€361.67` (`€0.00` price discount)** while adding **`index_risk = +€17,678.43`** to buyer cost `C` (`cost_breakdown`: `price=2,209,803.70`, `payment_terms=-14,732.02`, `quality=1,191.45`, `index_clause=0.0`, `index_risk=17,678.43`, `relationship=34,133.56`).
  - Furthermore, the session trace (`P02_2cd93357d9b549f0.json`) records:
    > `r1: agreed on 70510-1 (worth 363.94 vs counter 346.25 at plan)`
    Even without `CU-NOTATION`, our Round 1 offer of `€361.67` at `payment_days = 90` is worth `~€358.85` at standard terms—still **`€12.60/unit` above** the supplier's Round 1 counter target (`€346.25`). Without the uncompensated `index_clause`, `sup:70510` still agrees immediately in Round 1, `index_risk` drops from `€17,678.43` to `€0.00`, and `C_deal` drops to **`€2,230,396.68 <= C_best` (`score = 1.0000`, `+0.2653`)**.

---

### P08 — `sup:70710` (Edelstahl-Service Donautal GmbH, `price_increase` on 1.4404 bar)
- **Sessions compared:**
  - `P08_658cb9b2cca49b4e` (Reference best: **`0.7368`**, `C_deal = €1,006,527.18`, `€5.18` in 4 rounds, `offer:70710-4`)
  - `P08_0ea1315746135f54` (`hackit2` run 2: **`0.7147`**, `C_deal = €1,010,407.19`, `€5.20` in 3 rounds, `offer:70710-3`)
  - `P08_d3eb9b3383307ee3` (`hackit2` run 1: **`0.5376`**, `C_deal = €1,041,496.59`, `€5.36` in 3 rounds, `offer:70710-3`)
- **What happened & why the supplier stopped early:**
  1. **`HOLD_FACTOR = 0.7` slowed Round 2 and exhausted supplier patience 1 round early (`-0.022` to `-0.199`):**
     - In Round 1 of all three sessions, we open at `€4.76` (main, no index) + `€4.61` (alt, `AS-14404` 0% deadband), and `sup:70710` counters with `offer:70710-1 = €5.57` (adopting `AS-14404` 0% deadband).
     - In `P08_658cb9b2cca49b4e` (Reference), Round 2 steps by `STEP_SHARE = 0.30` to **`€5.00`** (`gap = (5.57 - 5.00)/5.57 = 10.2%`). The supplier stays below its patience threshold for 4 full rounds: `€5.57` (r1) -> `€5.43` (`offer:70710-2`, r2) -> `€5.23` (`offer:70710-3`, r3) -> `€5.18 [final_offer]` (`offer:70710-4`, r4).
     - In both `hackit2` runs (`P08_0ea1315746135f54` and `P08_d3eb9b3383307ee3`), `reply_reader` tagged `sup:70710`'s r1 message as `tactic = "hold"`, so `_concede` (`engine.py:390-393`) multiplied `STEP_SHARE` by `HOLD_FACTOR = 0.7` (`0.21`), stepping to only **`€4.93`** in Round 2 (`gap = 11.5%`). That extra `1.3%` gap pushed cumulative impatience over `sup:70710`'s limit in Round 3, triggering `final_offer` at `offer:70710-3` (`€5.20` / `€5.36`) and cutting off Round 4 (`€5.18`).
  2. **Verification claim ordering on a `hedged` supplier (`-0.177` between run 1 and run 2):**
     - `sup:70710` has trait `hedged` (material `AS-14404` is fixed-price hedged until March 2027). When we cite `idx:AS-14404@2026-06..09` (`-5.5%`) in Round 2, `sup:70710` rejects it: *"our AS-14404 is covered at fixed prices until March 2027 so that fall does not reach our cost... it does not change our calculation."*
     - Conversely, when we cite `idx:PPI-C2431@2026-06..08` (`-2.3%`, processing energy/conversion, 38% of cost), `sup:70710` confirms: *"We have verified the PPI-C2431 data you cited; it holds and reduces our cost basis slightly, which we pass on in our revised calculation."*
     - In `P08_d3eb9b3383307ee3` (`0.5376`), `PPI-C2431` was held back until Round 3 while r2 cited only `AS-14404` (which the hedged supplier ignored), so `offer:70710-2` barely moved (`€5.45` vs `€5.33` in `0ea13157`) and `offer:70710-3` stopped at `€5.36`. In `P08_61a1c05eed7f123d` (`0.6926`), citing `PPI-C2431` in Round 1 dropped `offer:70710-1` immediately to `€5.52` (vs `€5.57`).

---

### P18 — `sup:70740` (Blankstahl Werdenfels GmbH, 1.4404 bright steel)
- **Sessions compared:**
  - `P18_27a30752932fded8` & `P18_747cdc23786968bc` (Reference best: **`0.6883`**, `C_deal = €245,255.50`, `€5.00` with `AS-14404` `0.0%` deadband in Round 4, `offer:70740-4`)
  - `P18_2b8f22bc8bb1b4f2` (`hackit2` run 1: **`0.6295`**, `C_deal = €249,519.92`, `€5.20` with `AS-14404` `5.0%` deadband, `offer:70740-4`)
  - `P18_77e42eae4da89f43` (`hackit2` run 2: **`0.6295`**, `C_deal = €249,519.92`, `€5.20` with `AS-14404` `5.0%` deadband, `offer:70740-4`)
- **What happened & why we lost `0.0588` (`€4,264.42` in `C`):**
  1. **Round 1 prepared index alternative + omitted `PPI-C2431` raised `offer:70740-1` by `€0.20/unit` (`€5.50` vs `€5.30`):**
     - In `P18_27a30752932fded8` (`0.6883`), Round 1 sent a single fixed-price sheet (`€4.77`, no prepared index alternative) and cited **both** `idx:AS-14404@2026-06..09` (`-5.5%`) and `idx:PPI-C2431@2026-06..08` (`-2.3%`) alongside `po` history (`€5.84`). `sup:70740` countered in Round 1 at **`€5.30`** (`offer:70740-1`, `0%` deadband) + alt `€5.40` (`5%` deadband).
     - In `P18_2b8f22bc8bb1b4f2` and `P18_77e42eae4da89f43` (`0.6295`), the prep team attached a second Round 1 sheet with `AS-14404` (`€4.72` @ 5% deadband or `€4.62` @ 0% deadband), and in `77e42eae` `scorecard` displaced `PPI-C2431` from Round 1's 3-claim cap (`CLAIMS_FIRST_ROUND = 3`). `sup:70740` anchored its Round 1 counter at **`€5.50`** (`offer:70740-1`, `0%` deadband) + alt `€5.60` (`5%` deadband)—a `€0.20/unit` worse trajectory from Round 1.
  2. **`HOLD_FACTOR = 0.7` in Round 2 triggered `final_offer` at Round 3 (`€5.10`/`€5.20` vs `€5.00`):**
     - In `P18_2b8f22bc8bb1b4f2` and `P18_77e42eae4da89f43`, `reply_reader` classified `sup:70740`'s r1/r2 replies as `hold`, slowing our r2/r3 steps (`€4.77 -> €4.91 -> €5.01`) so `sup:70740` issued `final_offer` in Round 3 at `offer:70740-3` (`€5.10` @ 0% deadband / `€5.20` @ 5% deadband, where `€5.20` @ 5% deadband has `C = €249,519.92` vs `€250,160.61` for `€5.10` @ 0% deadband because `AS-14404` has a rising forward path in `P18`). In `P18_27a30752932fded8`, full `0.30` steps (`€4.77 -> €4.93 -> €5.02`) kept `sup:70740` negotiating to `€5.00` (`0%` deadband, `C = €245,255.50`).

---

### P11 — `sup:70140` (Odlewnia Kalwisz Sp. z o.o., EN-GJS-500-7 casting, fixed `payment_days=60, lead_time_days=56`)
- **Sessions compared:**
  - `P11_54778550410607b3` (Historical best: **`0.7935`**, `C_deal = €742,132.50`, `€45.00`, `ppm = 300`, `offer:70140-6`)
  - `P11_357382552336c6f9` (`hackit2` run 1: **`0.7099`**, `C_deal = €750,082.50`, `€45.50`, `ppm = 300`, `offer:70140-5`)
  - `P11_bdce324577e88d7e` (`hackit2` run 2: **`0.6723`**, `C_deal = €753,660.00`, `€45.50`, `ppm = 800`, `offer:70140-6`)
- **What happened (two distinct root causes totaling `0.1212` score / `€11,527.50`):**
  1. **Unsafe quality-loosening replan (`-0.0376` / `+€3,577.50` between run 1 and run 2; Task `T3`):**
     - In `P11_bdce324577e88d7e`, Round 1 sent main `€41.36` (`ppm = 300`) and a prepared alternative `€41.13` loosening `ppm_commitment` from `300` to `800`.
     - `sup:70140` countered in Round 1 with main `offer:70140-1` (`€46.50`, `ppm = 300`, `C = €765,982.50`) and alt `offer:70140-1-a` (`€46.00`, `ppm = 800`, `C = €761,610.00`).
     - Because `€46.00` (`ppm = 800`) was transiently cheaper by `€4,372.50` in Round 1 (`€7,950` price diff vs `€3,577.50` quality cost), `adopt_cheaper_package` (`hackit2/negotiation/packages.py:217-263`) **permanently replanned `thread.plan` onto `ppm_commitment = 800`**.
     - However, `sup:70140`'s floor with fixed 60-day payment terms is **`€45.50` under both `ppm = 300` and `ppm = 800`**! By Round 5 (`offer:70140-5`), the supplier converged to `€45.50` at `ppm = 800`, leaving us with zero unit-price savings and a permanent **`+€3,577.50` quality penalty** (`quality = €5,724.00` vs `€2,146.50`).
  2. **Missing Round 1 energy-index claim + aggressive late-round buyer stepping (`-0.0836` / `+€7,950.00` between `€45.50` and `€45.00`):**
     - In `P11_357382552336c6f9` (`0.7099`), `CLAIMS_FIRST_ROUND = 3` selected `scorecard`, `order_history`, and `forecast` in Round 1, deferring the verified `-6.1%` electricity index drop (`idx:PPI-D351@2025-11..2026-08`) to Round 2. Without an index claim in Round 1, `sup:70140` held at `offer:70140-1 = €47.00` (vs `€46.50` in `bdce3245` where `PPI-D351` was cited in Round 1).
     - Furthermore, in `P11_357382552336c6f9`, we stepped `30%` of the remaining gap every round (`€41.36 -> €43.05 -> €43.67 -> €44.22 -> €44.60`), reaching `98%` of `€45.50` by Round 5 so `sup:70140` held firm at `€45.50`. In `P11_54778550410607b3` (`0.7935`), buyer concessions tapered after Round 2 (`€40.90 -> €41.91 -> €42.16 -> €42.30 -> €42.44 -> €42.58`), keeping the buyer anchor at `€42.30` in Round 4 while pairing it with a firm quarterly call-off schedule commitment, which unlocked `offer:70140-4 = €45.00` (`score = 0.7935`).

---

## 2. Ranked Code-Owned Proposals

| Rank | Target Files & Functions | Proposal (Code-Owned Decision) | Cases Helped | Expected Score Gain | Risk & Mitigation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | `hackit2/negotiation/packages.py` (`offer_index_clause`, lines 146–164) | **Guard `offer_index_clause` against uncompensated `index_risk` when clamped by `sweet_spot` / `floor`:** Compute `new_price = round(max(thread.price - worth * 0.6, floor), 2)` *before* mutating `thread.plan`. If `cost > 0` (the clause adds buyer `index_risk` / expected cost) and the actual price reduction `thread.price - new_price <= cost * 1.2` (e.g., `0.00` on `P02` where `thread.price == sweet_spot`), do **not** add `index_clause` to `thread.plan` (and do not add it as a Round 1 prepared alternative when `new_price >= thread.price`). | **P02** (and any `stabilise` / `secure_supply` near-cost case with rising index) | **`+0.265` on P02** (`0.735 -> 1.000`, `-€17,678` in `C`; **`+1.33` pts** on 20-case mean) | **Very Low.** Pure arithmetic guard: never volunteer a buyer-costly index clause for `€0.00` price reduction. |
| **2** | `hackit2/negotiation/packages.py` (`adopt_cheaper_package`, lines 217–263; `prepared_alternatives`) | **Trade safety on quality terms (Task `T3`):** Never replan `thread.plan` onto an alternative package that loosens quality terms (`ppm_commitment > plan["ppm_commitment"]` or `chargeback < plan["chargeback"]`) unless its estimated floor cost is also strictly cheaper than the current plan at `thread.floor`, OR stop offering quality-loosening prepared alternatives (`ppm_commitment` relaxation) in Round 1 when `ppm_commitment` is already at the supplier's standard term (`std["ppm_commitment"] == 300`). | **P11** | **`+0.038` on P11** (`0.672 -> 0.710`, `-€3,577.50` in `C`) | **Low.** Prevents transient Round 1 `€0.50` spreads from locking in permanent quality penalties when both packages hit the same supplier price floor (`€45.50`). |
| **3** | `hackit2/settings.py` (`HOLD_FACTOR`), `hackit2/negotiation/engine.py` (`_concede`, lines 390–393) | **Neutralize or soften `HOLD_FACTOR` in early rounds (Task `T2`):** Set `HOLD_FACTOR = 1.0` (or apply `HOLD_FACTOR` only from Round `>= 3` when the supplier actually stalled price movement: `prev_counter - counter < 0.01 * quote`). In Round 1, every supplier opening counter (`offer:70710-1`, `offer:70740-1`) looks firm in prose (`tactic == "hold"`), so multiplying Round 2 `STEP_SHARE` by `0.7` (`0.21` vs `0.30`) inflates our Round 2 relative gap and burns supplier patience one round early. | **P08**, **P18** | **`+0.022` to `+0.199` on P08** (`€5.20/€5.36 -> €5.18`), **`+0.059` on P18** (`€5.20 -> €5.00`) | **Low–Medium.** Must be verified via `T2` simulator/sandbox A/B to confirm later rounds do not over-concede against true stonewalling. |
| **4** | `hackit2/briefing.py` / `hackit2/negotiation/engine.py` (`_next_arguments`) | **Prioritize verified falling cost/energy indices (`kind == "index"`, especially non-hedged processing/energy series like `PPI-C2431` and `PPI-D351`) in Round 1:** Ensure at least one verified falling index claim is included in Round 1's `CLAIMS_FIRST_ROUND = 3` budget (ahead of generic `scorecard` / `forecast` claims), and when a supplier has trait `hedged`, rank conversion/energy indices (`PPI-*`) ahead of raw-material indices (`AS-*`) so Round 1 & Round 2 concessions are unlocked immediately. | **P08**, **P18**, **P11** | **`+0.050` to `+0.177` on P08**, **`+0.030` on P18**, **`+0.020` on P11** (r1 counter drops `€0.05–€0.50/unit` earlier) | **Very Low.** Reorders existing deterministic `VerificationClaim` queue without adding LLM calls or changing `CLAIMS_FIRST_ROUND`. |
| **5** | `hackit2/negotiation/packages.py` (`prepared_alternatives`, lines 266–315) | **Defer prepared index-clause alternatives from Round 1 to Round 2 when `clause_cost_share > 0`:** When an index clause increases buyer `C` (`clause_cost_share(case, clause) > 0`, as in `P18` where `AS-14404` has a rising forward path), do not volunteer it as a Round 1 prepared alternative sheet; let Round 1 anchor on our fixed-price sheet + verified index claims first, and introduce the index alternative only if the supplier demands indexation or in Round 2. | **P18** | **`+0.030` to `+0.059` on P18** (prevents `sup:70740` r1 counter from anchoring at `€5.50` instead of `€5.30`) | **Low.** Preserves the fallback alternative for Round 2+ while keeping the Round 1 price anchor clean. |
