# Fixed alpha: three lowest-score cases, 25 October 2026

Burak authorized exactly three hosted practice sessions. Selection: the three lowest scores in the
previous alpha cohort, P08, P10 and P11. Code: `8213c40`; parallelism three, fresh research and an
isolated copy of the committed lessons. The model cap was 100, as previously authorized.

All three cases closed deals. Scores are on a 0-100 scale.

| Case | Historical main | Previous alpha | Fixed alpha | Delta vs previous alpha | Research strategy used |
|---|---:|---:|---:|---:|---|
| P08 | 72.58 | 65.94 | 73.68 | +7.75 | Yes |
| P10 | 67.07 | 67.07 | 66.29 | -0.78 | Yes |
| P11 | 70.99 | 70.99 | 70.99 | +0.00 | No: invalid synthesis JSON |
| Mean | 70.21 | 68.00 | 70.32 | +2.32 | 2/3 |

## Total cost

| Case | Previous alpha C (EUR) | Fixed alpha C (EUR) | Saving vs previous alpha (EUR) |
|---|---:|---:|---:|
| P08 | 1,020,119.10 | 1,006,517.51 | +13,601.59 |
| P10 | 1,527,674.40 | 1,532,385.60 | -4,711.20 |
| P11 | 750,082.50 | 750,082.50 | +0.00 |

Total saving versus previous alpha: EUR 8,890.39.
Against the historical main reference, total C was EUR 2,768.11 higher.
Unweighted mean score and aggregate euro savings are different objectives.

## Preparation reliability

P08 and P10 used their alpha synthesis strategies. P11's synthesis completed but its response
was invalid JSON: an object closed after the notes and additional fields followed outside it.
The parser rejected it and the numeric engine continued with the fallback preparation. This
was a formatting failure, not the previous synthesis timeout. The original P11 run also used
fallback preparation, so its unchanged score does not measure nine-agent research quality.

The P08 final price fell from EUR5.25 to EUR5.18; P10 rose from EUR31.60 to EUR31.70.
No code or prompts were changed during this trial. These are single runs compared with historical
results; model/supplier variation and changes in preparation prevent a causal improvement claim.

## Artifacts and spend

Local artifacts: `logs/alpha-worst3-20261025-024231`. Manifest, summary, comparison, full session/back-office data, frozen
briefs and trial-scoped live journals are preserved. Live UI: http://127.0.0.1:8793.

Shared model spend rose by 2.215608 to 39.969188 during the batch,
on the application's accounting scale; the ledger is shared with other agents and this is not an
exclusive billing measurement. Practice remaining reported: 178. Exactly three
sessions were opened by this trial; concurrent team activity can also change the shared quota.
