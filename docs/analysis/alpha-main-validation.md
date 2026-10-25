# Integrated alpha: three-case validation and main promotion

Burak authorized three hosted practice cases, a subsequent bug review and promotion to main.
The tested integrated build was `553695e`; it includes nine-role research and the reviewed
**hackit2 Live** control-room website. P08, P10 and P11 were selected to match the previous
worst-three retest. Parallelism was three, research was fresh, lessons were isolated, and the
explicit model cap was 100 with the existing shared spend ledger.

## Hosted results

All three sessions closed deals and passed must-haves. Scores use a 0–100 scale.

| Case | Historical main | Previous fixed alpha | Integrated build | Research strategy applied | Preparation seconds |
|---|---:|---:|---:|---|---:|
| P08 | 72.5756 | 73.6822 | 72.5756 | Yes | 32.539 |
| P10 | 67.0711 | 66.2905 | 67.0711 | Yes | 32.909 |
| P11 | 70.9896 | 70.9896 | 70.9896 | Yes | 28.051 |
| Mean | 70.2121 | 70.3208 | 70.2121 | 3/3 | |

| Case | Integrated buyer C (EUR) | Saving vs previous fixed alpha (EUR) |
|---|---:|---:|
| P08 | 1,008,460.60 | -1,943.09 |
| P10 | 1,527,674.40 | +4,711.20 |
| P11 | 750,082.50 | 0.00 |
| Total | 3,286,217.50 | +2,768.11 |

The integrated scores and costs equal this three-case historical main reference. The mean is
0.1087 points below the previous fixed-alpha retest, while its aggregate euro cost is lower.
These are single runs with fresh model output and supplier variability, not a controlled causal
comparison. The results establish working integration, not a demonstrated score advantage.

Every case produced nine reports with 36 sourced findings and an engine-applied strategy. P11's
previous malformed-synthesis failure did not recur. Citation validation establishes retrieval,
not semantic truth or usefulness. Four reply-reader calls timed out; the guarded fallback
continued negotiation. All twelve writer paragraphs succeeded. No must-have failure or lost
deal was observed.

Exactly three hosted sessions were opened:

- P08: `8aa697e5ee9ea0bb`
- P10: `46f2eaacc7e8b8d7`
- P11: `02e48e39bd1c1ab6`

## Bug review and fixes after the hosted run

1. **Schema discovery still used the shared SQLite connection.** Ordinary Scout SQL already had
   an isolated connection, but `schema()` delegated to `DataPackage.tables()`, which runs PRAGMA
   calls on the shared connection. It now uses its own read-only connection, quotes table names
   and enforces a deadline. A regression test reproduces the forbidden shared access before the
   fix and passes afterward. This was found by code inspection; the hosted batch had no schema
   lookup errors.
2. **The alpha synthesis prompt split its output instructions.** It showed a closed strategy
   object and then requested additional playbook fields. The previous P11 response placed fields
   outside the object. Alpha now supplies one complete valid JSON example and explicitly keeps
   every field inside one object. Existing trade constraints and numerical clamps remain. The
   parser still rejects malformed JSON: this reduces ambiguity rather than guaranteeing that a
   model never produces invalid output.
3. **Zero-time-budget helpers had no caller outcome event.** They returned fallback correctly,
   but a later successful model response could appear without a recorded timeout decision.
   These calls now emit the timeout outcome. This is telemetry only; late responses still cannot
   change the decision.

Regression checks failed on the three old behaviors before the fixes. No beta concession,
acceptance, index-selection, quality-ban or budget-override policy was added. The original
four-specialist pipeline remains available with `--research-mode baseline`.

## Post-fix verification

- Full local suite: **169 passed, 2 skipped**, including saved-case simulator and UI renderer checks.
- Ruff lint, formatting and Git whitespace checks pass.
- Sixteen parallel schema/SQL checks on the actual package completed successfully; the isolated
  schema exactly matched the package's 24-table schema.
- Real-model P11 research-only validation of the complete JSON contract: **36.695 seconds**,
  one of one supplier prepared, nine reports, 36 sourced findings, seven tool calls; no invalid
  JSON or model errors. Eleven collected helper responses succeeded; one late research helper
  was not awaited. It opened **zero** arena sessions.
- No fourth hosted practice case was opened after the fixes; their downstream effect on hosted
  negotiation scores has not been remeasured.
- README and agent instructions describe the nine-role default, baseline switch and validation
  limits. The website name remains **hackit2 Live**.

## Artifacts and accounting

Artifacts are local under `logs/alpha-main-validation-20261025-030808`: manifest, summary,
trial-scoped live events, frozen briefs, isolated lessons, full sessions and ended-session
back-office data. An archive filename error in the local runner was corrected and the three
completed sessions were archived afterward; session outcomes were already saved.

The shared spend counter rose from 39.969188 to 41.808660 during the hosted batch, a delta of
1.839472 on the application's accounting scale. The separate research-only check costs extra.
Other team activity can affect this shared ledger and the reported practice quota; neither is
an exclusive billing measurement. No shared environment file was inspected, edited or committed.

Persistent local UI: http://127.0.0.1:8793.
