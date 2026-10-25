# Alpha: ten-case hosted practice, 25 October 2026

## Result

Exactly ten hosted practice sessions were authorized and run, parallelism three, using alpha
`74aad2526885702c044b6f977fe7b3d202b4d9f2`. All ten closed deals. Mean score: **78.9231/100**.
Historical main/integration reference: **78.2585/100**; difference **+0.6645 points**.
This is a historical comparison, not a controlled A/B result: no fresh baseline cohort was run.

| Case | Historical reference | Alpha | Delta points | Supplier briefs used / expected |
|---|---:|---:|---:|---:|
| P01 | 99.91 | 99.97 | +0.05 | 1/1 |
| P02 | 73.47 | 73.47 | +0.00 | 0/1 |
| P03 | 92.28 | 92.28 | +0.00 | 0/1 |
| P04 | 86.04 | 86.04 | +0.00 | 1/1 |
| P05 | 82.18 | 82.18 | +0.00 | 2/3 |
| P07 | 75.10 | 75.10 | +0.00 | 0/1 |
| P08 | 72.58 | 65.94 | -6.64 | 0/1 |
| P10 | 67.07 | 67.07 | +0.00 | 0/1 |
| P11 | 70.99 | 70.99 | +0.00 | 0/1 |
| P18 | 62.95 | 76.18 | +13.23 | 0/1 |

The summed buyer cost was EUR 2,031.83 **higher** than the historical reference,
despite the higher unweighted score average. Cases have different score denominators; a score
improvement and an aggregate euro saving are different measurements.

Recorded model spend for the practice batch: 6.180972 on the application's 1:1
USD/EUR accounting scale. Shared spend rose from 30.112556 to 36.293528; the user authorized
a cap of 100 for this run. The shared counter was preserved. Practice remaining reported: 197.

## Research did not reliably reach the engine

Only **4/12 supplier briefs** were used, across **3/10 cases**.
Seven cases received no preparation brief and used the original numeric policy with the live reader/writer.
Therefore the score does not establish that the nine-agent design outperforms the original preparation.

- P03 hit a parallel access error while discovering the database path using PRAGMA on the shared
  SQLite connection. The session caught it and continued with the safe engine.
- Several synthesis responses took 14-26 seconds. Alpha incorrectly inherited a 13-second wait
  cap for this stage, discarding responses even when the total 40-second budget could fit them.
- Specialists generally returned sourced reports. The observed bottleneck was often delivery of
  the synthesis result, not failure to collect source data.
- Scout made four tool calls across the batch, with no tool errors. Additional model turns can
  exceed the research-stage deadline; a request is not proof its result influenced the strategy.

The biggest score changes were P08 (-6.64 points) and P18 (+13.23 points). Both had no alpha brief.
P08 opened with index/index/hedged instead of the reference's hedged/scorecard/order-history;
P18 opened with index/index/order-history instead of order-history/scorecard/index. Reader tactics
and subsequent offer paths also differed. These observed differences are not isolated causal tests.

## Follow-up fixes and research-only validation

The live practice process kept the original commit loaded for all ten cases. Fixes were prepared
separately during the run and apply only to subsequent runs:

1. Discover the package database from its public root, matching DataPackage's selection, without
   querying the shared connection. SQL itself already uses private read-only connections.
2. Let synthesis use the remaining overall preparation budget instead of the inherited 13-second
   stage cap. The total preparation budget remains 40 seconds.

New regression tests cover both issues. Full local suite: **155 passed, 2 skipped**, plus Ruff
lint/format. No additional hosted practice sessions were opened for validation.

Real-model research-only checks after the fixes:
- case:P02: 31.068 seconds; 1/1 suppliers prepared, nine reports, zero helper failures, zero arena sessions.
- case:P03: 34.988 seconds; 1/1 suppliers prepared, nine reports, zero helper failures, zero arena sessions.

These checks validate preparation reliability on two examples, not negotiation score improvement.
A future hosted comparison should use the fixed commit and explicitly measure brief coverage,
source quality, latency, score, total cost and timeout rates across repeated cohorts.

## Local artifacts

- `logs/alpha-practice-20261025-022528/manifest.json`: exact commit, cases, parallelism and budget.
- `summary.json`, `comparison.json`, `main-reference.json`: results and historical comparison.
- Per-session JSON files: full arena state and back office.
- `briefs/alpha-nine-v1/`: accepted frozen alpha preparation.
- `research-fixes/`: source evidence and model telemetry for the two research-only checks.
- Live journals: `logs/live/case_P*.jsonl`; persistent alpha UI: http://127.0.0.1:8793.

Artifacts are local and not committed. Main and beta were not changed.
