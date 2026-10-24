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

Results and the final retention decision will be added after the authorized cohort completes.

## Checks

`uv run pytest -q`: **103 passed, 3 skipped**, including 30 experiment tests. Ruff lint and format
checks passed. Tests cover optionality, delivery/undo/resume, final offers, attribution, negation,
retractions, actual reply paragraph forms, supplier instruction isolation, buyer-cost and must-have
gates, quality protection, and complete scored simulator sessions with the flag both OFF and ON.
