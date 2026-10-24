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
and hard quota enforcement. Results will be added when the authorized cohort is complete.
