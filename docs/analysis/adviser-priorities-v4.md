# T21: seller adviser value discovery, controlled v4 experiment

Burak authorized execution of OpenCode's revised T13 plan and at most 16 practice sessions.
Codex implements the feature and experiment; Claude supplied frozen preparation and the writer
guard in integration commit `775e76a`. OpenCode reviewed the implementation before practice.

## Frozen implementation

- Default OFF: `HACKIT_SELLER_ADVISERS=1` enables the treatment per process.
- Round one has no adviser processing or question. A targeted question can be sent once, in round
  two, only if a case-allowed, must-have-valid change remains in a seller-need direction: earlier
  payment, more volume within our actual demand, later delivery, or a longer contract.
- Name only relevant departments. End the question with: "If we can meet those needs, which price
  could you offer us in return?" No model sets numerical commitments or acceptance decisions.
- Keep the existing strictly-cheaper `need_alternatives` path unchanged. After a question is
  delivered, additional probes can respond to explicitly stated needs if slots remain. A field
  already covered by a legacy alternative is excluded; at most two alternatives total are sent.
- Code solves the buyer-cost-neutral price of each probe, locating the minimum before bisection
  when relationship cost is convex. Rounding, exact term options, must-haves, acceptable cost,
  buyer cost no greater than the primary, and the latest quote share cap are all checked.
- Each probed lever is shown once. A probe never changes the primary plan by itself. The existing
  replan path for a strictly-cheaper legacy trade is preserved. A seller reply matching extra
  sheet terms is recorded as a match, not assumed proof of a specific internal valuation.
- Delivery undo and crash resume preserve the once-only inquiry rule; no new inquiry after a
  final offer or agreement. Source and preparation hashes are fixed across the cohort.

## Eligibility and selected cases

The first loader incorrectly treated missing `moves[]` in summary session archives as absence of
counteroffers. OpenCode caught this before any practice session opened. The corrected offline
check uses full `threads[supplier].messages` in `/tmp/opencode/compare/data/ours/<case>.json`, the
current official case files and data package, and archived standard terms from run journals.
It rebuilds the primary plan through the archived first counter, then tests available directions.
Detailed plans and sources are saved in ignored `logs/adviser-experiment-v4/eligibility.json`.

| Case | Remaining valid directions at the archived round-two plan |
|---|---|
| P01 | None |
| P02 | None |
| P03 | Earlier payment |
| P04 | Earlier payment |
| P05 | Later delivery (two bidders) |
| P07 | Earlier payment |
| P08 | Earlier payment |
| P10 | None |
| P11 | None; payment/volume fixed, only quality terms negotiable |
| P18 | Earlier payment |

OpenCode approved **P03, P04, P05 and P07**, two OFF/ON pairs per case, total **16 sessions**, at most
two cases active concurrently. Each pair runs back to back; the second pair reverses arm order.

## Controls and evidence

`scripts/adviser_experiment.py` prepares and freezes a per-case brief before any session opens.
Every worker checks the frozen brief and source hashes. Fresh processes run each arm and
`learn=False` prevents lesson drift. Opening reader/writer outcomes are cached after timeout and
guard handling, including `None` fallback markers; late raw answers cannot replace a fallback.
Keys include helper source versions and their full inputs. Cache hit/miss counts are separate
from actual model-call counters, and actual round-one message hashes are recorded for comparison.

An attempt ledger caps this cohort at 16 session-open attempts and prevents automatic retries of
uncertain opens. Per-trial file locks allow completed results to be reused without spending more
quota. The harness saves public exchanges, round-two counters, extra-sheet matches, final terms,
rounds, wall time, delivery timeouts, writer rejections and completed-session backoffice. The buyer
never uses the backoffice. Shared spend deltas include concurrent calls and are not per-run costs.

Start command:

```sh
uv run python scripts/adviser_experiment.py --practice --cases P03 P04 P05 P07 --pairs 2 \
  --parallel 2 --output logs/adviser-experiment-v4/cohort
```

## Predeclared decision

Keep and merge enabled only if ON mean score is at least OFF mean + **0.02** over the eight pairs,
and no case's ON mean is more than **0.02** below its OFF mean. Invalid must-haves or failed
experimental controls cannot justify adopting a feature. Otherwise leave the experiment on
`agent/codex` and do not merge it into `burak`.

## Validation and results

Before practice: **156 tests passed, 3 skipped**, Ruff lint and format checks clean. This includes
42 feature/harness tests covering neutral-cost roots, must-haves, quote limits, actual levers,
delivery/resume, unchanged opening, preservation of legacy trades, first-turn fallback replay,
and hard quota enforcement. Implementation was frozen at `426a6d9` throughout the cohort.

All **16 authorized practice sessions completed**, with valid deals and no must-have failures.
Scores below are on the 0–100 scale; each arm's case mean includes two sessions.

| Case | OFF mean | ON mean | ON minus OFF |
|---|---:|---:|---:|
| P03 | 92.4510 | 92.4032 | -0.0479 |
| P04 | 86.0445 | 86.0445 | 0.0000 |
| P05 | 82.4945 | 82.1845 | -0.3100 |
| P07 | 75.0971 | 75.5003 | +0.4031 |
| Overall | **84.0218** | **84.0331** | **+0.0113** |

The gain is below the predeclared **+2-point** threshold. No case breached the loss guard, but
the improvement requirement failed. **Do not merge or enable this experiment for production.**
Keep the default OFF and retain implementation and evidence on `agent/codex`.

### Pair evidence

| Case / pair | OFF session | ON session | OFF score | ON score | Opening identical |
|---|---|---|---:|---:|---|
| P03 / 1 | `9828a2331b438a15` | `410790115a134763` | 92.4510 | 92.4510 | Yes |
| P03 / 2 | `15a5a4505949f220` | `1057fba5305c73bd` | 92.4510 | 92.3553 | Yes |
| P04 / 1 | `ce55206be717b4d6` | `dcc8df77f0a2e96f` | 86.0445 | 86.0445 | Yes |
| P04 / 2 | `d64f17c1246d66fa` | `e52123e65ab0792e` | 86.0445 | 86.0445 | Yes |
| P05 / 1 | `9c518586460dc33f` | `2b8fd5620dbae434` | 82.8045 | 82.1845 | No |
| P05 / 2 | `3257f96c7e7cd34d` | `c2b223f71efff669` | 82.1845 | 82.1845 | Yes |
| P07 / 1 | `11629ac632a164d8` | `884902e417f55a85` | 75.0971 | 75.7530 | Yes |
| P07 / 2 | `7947b424433a0fea` | `323fb9d5abe0c851` | 75.0971 | 75.2475 | Yes |

Every pair used the same frozen preparation brief, all brief hashes remained unchanged, and one
source fingerprint covered all trials. Actual opening messages matched in **7 of 8 pairs**. P05
pair one had a delivery timeout in OFF and a different RFQ timing/counter path; its score loss
cannot be attributed solely to the inquiry. Delivery timeouts totalled OFF 1, ON 0. Later model
reading/writing and supplier responses remained live, so small score differences are descriptive,
not proof of causality.

All eight ON sessions delivered one targeted question. **No new cost-neutral value probe was
generated in the cohort.** The solver and integration passed unit tests, but these sessions do
not demonstrate that the new probe path improves deals. They primarily evaluate targeted inquiry
alongside the existing alternative-package logic. This limits conclusions about the broader idea
of seller-team value discovery.

For example, P03's seller communicated a payment-related price alternative after the question,
but both arms ultimately settled on the same terms in pair one. Receiving a departmental need
does not itself establish an additional buyer-profitable trade. In P05, the frozen preparation
already contained an alternative for one bidder with later delivery, so some usable trade
information was already available before the inquiry.

A separate read-only observer was added after ten sessions had completed, following Burak's
request to show running tests in the watch UI. It polled public session/thread messages and wrote
telemetry without changing frozen buyer source or opening sessions. Its extra API load, cache
reuse and concurrent load prevent a causal latency comparison. The observer has stopped.

Raw artifacts are retained locally under ignored `logs/adviser-experiment-v4/cohort/`:
`manifest.json`, `summary.json`, frozen `briefs/`, opening outcome caches, the attempt ledger, and
per-trial `metrics.json`, `session.json`, `exchanges.json` and run journals. The full authorized
16-session allowance was used; no further practice was started. The practice claim was released
immediately after completion.
