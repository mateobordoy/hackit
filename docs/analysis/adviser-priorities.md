# Seller adviser priorities experiment (T13)

Burak requested that our buyer ask Sales about its colleagues' requirements, use volunteered
information to find a better deal, and keep the feature only if comparative tests support it.
Codex implemented the experiment; OpenCode independently reviewed the design and the first replay.

## Implementation and source

The source is slide **05, How a supplier works**, physical page 7 of the
[challenge presentation](../../../Tacto%20Challenge%20Presentation.pdf). Sales consults Management
(larger concessions), Engineering (drawings/specifications), Quality (ppm/complaints), Logistics
(capacity/delivery) and Finance (costs/payment). The question uses these actual department names:

> If you are comfortable sharing, could you outline the priorities of your finance adviser and your
> management, engineering, quality and logistics colleagues for this agreement? Understanding what
> each team needs may help us find a better offer for both sides.

`HACKIT_SELLER_ADVISERS=1` enables the experiment. The default is **OFF**. It adds this question to
the first active proposal for each seller, replacing the template's existing open question and
disabling a separate writer question. Delivery failure does not spend the question; resumed sessions
recover whether it was already delivered. Closing and final-offer mirrors never add it.

Explicit needs are extracted from the seller's public replies, with department attribution within
one sentence or one short paragraph naming exactly one department. Evidence excerpts stay in the
decision journal. Negations do not create trades, explicit retractions remove earlier hints, and
casual department mentions preserve prior needs. Ambiguous multi-department clauses are ignored.
The writer receives fixed descriptions of needs, never supplier-supplied instructions or numbers.

Earlier payment, firm volume, delivery flexibility and longer commitment feed the existing
`need_alternatives` enumerator. Only allowed options that pass must-haves, acceptable total cost and
an actual buyer-cost improvement can be offered. No supplier statement changes valuation, price
authority, acceptance, or quality protections. Engineering, quality and approval needs supply
context rather than automatic concessions. No internal backoffice data is used by the buyer.

## Method

The harness is `scripts/adviser_experiment.py`. Each arm runs in a fresh process with the same
data, model and settings apart from the feature flag. Trials alternate OFF/ON and reverse the
second pair. `learn=False` prevents memory changes between arms; live UI output is off. Sessions,
public exchanges, backoffice (only after completion), send durations and decision journals remain
under ignored `logs/adviser-experiment/`. No kit code was changed.

The sandbox cohort used deterministic buyer templates without buyer LLM calls: two pairs per case.
The first D02 ON trial was exploratory v1: the reader could only attribute needs within sentences.
Subsequent v2 trials also support an unambiguous department paragraph, based on that real reply.
The question and pricing gates were unchanged. v1 missed an inline finance priority; v2 captured
`finance/earlier_payment` and `quality/quality_protection` in D02, but found no new cheaper trade.

P11 ON also used v2. Its replay exposed a false Quality attribution: mentioning a *quality record*
in a paragraph about faster payment is not a request from the Quality department. The frozen v3
parser requires an explicit Quality team/manager/department, a labeled heading or a request verb.
P10 and P05 ON use v3. This correction has a regression test. No question, price rule or pace was
changed between these versions; the version changes still limit pooled comparisons.

Repeated identical exchanges returned in roughly a second. This suggests server response caching;
repeated runs must not be treated as independent stochastic samples. First-run timings also include
server load, other agents' activity and attachment processing. They do not establish that the
question alone caused the observed delay. The spend delta in run files is the **shared** process
budget delta; concurrent production calls can make it nonzero even for a template trial.

## Sandbox results

Scores below are on a 0-100 scale. All eight trials closed valid deals with zero delivery timeouts.

| Case | Pair | OFF score | ON score | OFF seconds | ON seconds | OFF session | ON session |
|---|---:|---:|---:|---:|---:|---|---|
| D02 | 1 | 67.161 | 67.161 | 48.698 | 175.060 | `97b5b6b9b11d2195` | `cd190a143f790346` |
| D02 | 2 | 67.161 | 67.161 | 29.468 | 1.733 | `61170c3f06fdbc57` | `398e2b8a9455f13b` |
| D01 | 1 | 99.149 | 98.753 | 0.883 | 111.517 | `96bb406dfc6358a2` | `5b6aac7599a79e57` |
| D01 | 2 | 99.149 | 98.753 | 0.829 | 0.972 | `e6c2024e094fb61c` | `42f0e3232cbdcaa0` |

D02's first C1 counter was 15.60 in both arms. J1 countered 14.90 OFF and 15.10 ON, then both
closed at 13.90. D01 countered 50.40 OFF and 50.50 ON. OpenCode independently confirmed an extra
Quality consultation in the first D02 ON backoffice; Freight Forwarder consultations also occur
in historical OFF sessions. The added finance information was already covered by the ordinary
payment-request detector. Sandbox evidence does not justify enabling the feature.

## Practice validation

After Burak explicitly authorized practice tests, Codex acquired `@arena-practice` for a bounded
six-session cohort: P11, P10 and P05, one OFF/ON pair each using the production preparation team,
reply reader and writer. These are screening pairs, not statistical proof. Keep the default OFF
unless gains justify further evaluation without worse must-have validity or clock reliability.

After the sandbox cohort ended, P05 ran alongside P11/P10, with at most two active practice sessions.
Per-trial file locks and completed-result reuse prevented duplicate session starts when the main
coordinator reached P05. The total remains six authorized practice sessions.

| Case | OFF score | ON score | Delta (points) | OFF session | ON session |
|---|---:|---:|---:|---|---|
| P11 | 79.351 | 70.990 | -8.362 | `b4c9ebdd36a4d3f9` | `f455376de5c0f77b` |
| P10 | 67.852 | 67.852 | 0 | `c4c6614c00a3d66b` | `8a82ce8195b2fe85` |
| P05 | 82.185 | 82.185 | 0 | `57d08e13fdcf5203` | `f79757cf8e112f4e` |

P11's first sheet was identical in both arms (41.36, 300 ppm, zero chargeback); final terms also
matched except for price: 45.00 OFF and 45.50 ON. Its first counter was 46.50 OFF and 47.00 ON.
However, the LLM lead chose different first-round evidence: OFF cited the forecast/index/history,
ON cited quality/index/history. This preparation variance confounds attributing the score decrease
solely to the adviser question. P10 had identical first sheets, evidence and counter (31.70).
Neither pair generated a new cheaper need-based alternative. Both pairs closed valid deals without
delivery timeouts. P11 took 268.927/258.926 seconds OFF/ON; P10 took 74.409/74.872 seconds.

P05 closed the same deal in both arms (27.92 per unit, payment 14 days, lead time 35 days,
24 months, DAP). The initial sheet for every bidder matched; evidence matched for the first two
bidders, while the last bidder saw different rival-offer IDs because the preceding paths differed.
Its first counters were 31.00/30.00/30.50 OFF and 31.00/30.50/30.50 ON. ON captured a Quality need;
there was one cost-checked alternative event, without an improved final deal. Wall time was
173.359/172.024 seconds, with one/zero delivery timeouts OFF/ON. All six practice sessions closed
valid deals. Timeout results and elapsed times are observations from this small, partly concurrent
cohort, not estimates of the question's isolated causal effect.

## Decision

**Do not enable or merge the feature into the production integration branch.** No tested case
improved, P11's screening pair was worse, and the sandbox showed no benefit. The sample and
preparation/version/cache confounders do not prove the idea can never help; they give no positive
evidence for adopting it now. The complete opt-in implementation and this report are retained on
`agent/codex` for review, with the flag default OFF. `burak` remains unchanged by this experiment.

The authorized six-session practice cohort is complete and the `@arena-practice` claim has been
released. No extra practice sessions or unrelated experiments were started.

## Checks

`uv run pytest -q`: **104 passed, 3 skipped**, including 31 experiment tests. Ruff lint and format
checks passed. Tests cover optionality, delivery/undo/resume, final offers, attribution, negation,
retractions, actual reply paragraph forms, supplier instruction isolation, buyer-cost and must-have
gates, quality protection, and complete scored simulator sessions with the flag both OFF and ON.
