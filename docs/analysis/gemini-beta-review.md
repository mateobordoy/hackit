# Gemini beta review and alpha UI integration

Reviewed beta `511c3aaf3889451cfb259433ad5353194b84b1ad` against main `823c2cf` and
alpha `8560def`. The beta working tree also has three uncommitted lesson rows; those were not
imported. Source inspection used a frozen Git archive, not a moving worktree. This review does
not assume that earlier chat descriptions still match the current branch.

## Decisions

| Area | What the current code does | Assessment | Alpha action |
|---|---|---|---|
| Control-room website | Buyer boundary, external Seller/Data, inspectable connections, clearer layout and cost display | Useful for inspecting real recorded exchanges; much of the map originates in shared commit `009e1f5` | Ported and adapted; brand remains **hackit2 Live** |
| Nine-agent research | Beta still uses the original preparation pipeline | Not a replacement for alpha research | Kept alpha's eight experts, Scout and synthesis unchanged |
| Budget | `budget_eur()` silently turns an explicit environment value of 30 into 150, and defaults to 150 | This fails to honor an explicit configured limit. Our authorized budget is 100 | Not imported; preserve explicit process configuration and the shared spend ledger |
| Historical cost chart | Recomputes every historical price using the current plan | These are hypothetical normalized costs, not each historical package's own C | Kept alpha's complete-package cost history. Old beta records are labelled as having an unrecorded calculation basis |
| Numeric legacy cost records | Beta sheet costs are numbers; alpha costs include component dictionaries | Copying renderers blindly loses information or fabricates a zero breakdown | Support both formats; show when components were not recorded |
| Early cheaper-package adoption | Uses finite `buyer_cost()` instead of the final `acceptable_cost()` gate | Sensible separation: an intermediate package may improve relative C even while above the fallback. Closing still needs its own gate | Candidate for an isolated policy experiment; not bundled into a website change |
| Quality guard | Never proposes/adopts a package with worse ppm or smaller chargeback | Addresses an observed P11 pitfall but is broader than the original round-one risk. It can block an economically useful intermediate trade | Not copied as a universal policy |
| Prepared trade surplus | Interpolates between equal-supplier-worth and equal-buyer-C prices | Plausible experiment. The seller-value model is estimated; price midpoint is not a universal proof of equal welfare sharing | No asserted score gain; retain existing alpha behavior pending evaluation |
| Index-clause C guard | Adds a current-C comparison before adopting a clause | Economically reasonable locally, but the search reference and subsequent negotiation matter. Similar T18 behavior previously regressed | Not blindly reintroduced; frozen P02 replay showed the same opening in current beta and alpha |
| First-round indices | Can reserve all three opening claim slots for index topics. Hedged suppliers postpone all index topics | Citable data does not imply seller acceptance. Blanket hedging treatment can suppress unrelated energy/processing evidence | Keep as a hypothesis, not a universally safe improvement |
| Hold pace | Ignores hold only on the second offer for a hedged supplier | Narrower than the earlier claim of ignoring hold for every supplier's second offer. Worth testing, but not proven universally better | Not imported without an isolated comparison |
| Touchy protection / needed lead time | The originally advertised first-concession exemption and `ask_needed_lead_time` are absent in current beta; concession math matches main | Earlier descriptions are stale after later edits/reversions | No fictional improvement credited |
| Lessons | Beta includes additional scored-run observations | Useful observations, not established causal rules. Copying changes the comparison input | Alpha's isolated lessons and research caches are preserved |

## Direct checks

### Quality guard is not a universal total-cost optimum

A local legal variant of simulator S01 made ppm negotiable with options 300/800 and a
quality-first goal. At the same remaining package terms, a price-15 / ppm-300 package cost
EUR102,077.50; price-10 / ppm-800 cost EUR69,160.00 and passed `exact_package` validation.
The beta quality predicate marks the latter as loosening quality regardless of this C reduction.
This is a counterexample to a universal planning rule, not a prediction about P11. The closing
desk can still compare supplier alternatives by C; the restriction is on offering/replanning.

### Current beta's P02 opening did not reproduce the old T18 regression

Using the same saved P02 opening, exact recorded standard terms, case and package for both engines:

- alpha: opening EUR361.67, CU-NOTATION clause (20% share, 5% deadband), sweet spot EUR361.67;
- beta: the same opening, clause and sweet spot;
- both estimated C = EUR2,251,054.53 for that opening package.

Thus the old T18 loss is a reason for careful testing, not proof that the current beta guard
necessarily repeats it. Likewise, an unchanged opening does not establish identical later behavior.

### Scores do not support guaranteed improvements

The latest recorded beta files included P10 67.8516, P18 62.0676 and P08 62.6155; earlier recorded
P08 results included 69.2555 and 54.8790. These files represent different revisions/conditions,
not one controlled cohort. They do not justify treating the latest P08 change as a proven recovery
or comparing a collection of best-per-case scores with a single-run average.

## UI integration details

The alpha website now uses the control-room layout and recorded-flow backend, with:

- the existing **hackit2 Live** name, a separate Scout module and eight-expert research description;
- actual Scout model, lookup and report exchanges connected to Data and the synthesis team;
- alpha's evidence ledger, full C components, unit-price-versus-C curve and per-package cost history;
- an explicit count of strategies actually applied by the negotiation engine, including fallback;
- backward-compatible numeric costs without inventing missing component breakdowns;
- labelled listed-run mean, avoiding the implication that the cross-worktree header is one alpha cohort;
- existing escaping, source provenance, captured-excerpt labels, keyboard access and mobile layout.

No beta pricing, concession, acceptance, model-budget, research or lesson changes were merged.
The website records and displays evidence; it does not issue negotiation actions or use private
seller back-office information as buyer context.

## Validation

- Frozen beta source: 138 tests passed, 3 skipped. These tests check mechanics, not score gains.
- Integrated alpha: 166 tests passed, 2 skipped, including saved-case simulator checks.
- Ruff lint and format checks passed.
- Actual headless Chromium inspection verified branding, desktop/mobile layout, the Scout diagram,
  a recorded lookup inspector and research/total-cost views. No browser exceptions were recorded.
- No additional hosted practice sessions or model calls were needed for this review.
