# Hypothesis learning (branch `feature/hypothesis-learning`)

Off by default. Switch on with `--hypotheses on` or `HACKIT_HYPOTHESES=1`. No practice session was run and nothing was pushed to `main`.

## What it does

1. **Agents propose.** Each of the nine research agents (eight experts and Scout) returns up to three testable hypotheses in the same report it already writes, so there is no extra model call and the 40 s preparation budget is unchanged. A hypothesis has these parts:
   - a move: a trade `field = option`, or an argument topic to lead with;
   - an expected reaction:
     - `takes_term`: the supplier puts that exact term into one of its sheets;
     - `price_drop`: its next counter falls clearly faster than its own previous step, by at least one percentage point more;
   - a rationale, the source IDs it rests on, what is uncertain, and a starting probability.
2. **Code cleans and merges** (`hypotheses.clean`, `hypotheses.merge`).
   - A hypothesis is kept only if it has an allowed field and option, a known reaction that fits the move, a probability in 0–1, and at least one source the agent was actually given.
   - The same move and reaction from several agents become one hypothesis with all agents and sources. Its starting probability is the mean of their estimates: agents reading the same records are not independent votes.
   - Probabilities are **not normalised**; two independent hypotheses can both be likely.
   - A stated 0 or 1 is clamped to 0.02 or 0.98, so later evidence still counts.
3. **Code validates and plays** (`Negotiator.check_hypotheses`, `play_hypothesis`). Before each message, every hypothesis is checked against the sheet about to go out:
   - **Trade:** the one-term change must be priced by the existing `prepared_alternatives` so it costs us no more than our main sheet, inside the trade price bounds, with must-haves kept. It must not already be our plan or already offered.
   - **Argument:** unused, citable evidence on that topic must exist.

   The valid hypothesis with the highest current probability is played:
   - a trade becomes the first alternative sheet;
   - an argument moves its evidence to the front.

   Our price, the numeric terms and accept/walk stay with the engine.
4. **Code learns** (`Negotiator.learn_hypotheses`, `hypotheses.observe`, `hypotheses.update`). Only the hypotheses that a delivered message actually tested are judged against the supplier's next reply, with fixed rules and Bayes in odds form:

   | Outcome | When | Update |
   |---|---|---|
   | supported, trade | any of its sheets carries F = V | odds × 3 |
   | refuted, trade | its e-mail explicitly refuses the term, or it moves F to another value | odds × 1/3 |
   | supported, price drop | its fall ≥ its previous step + 1 point | odds × 3 |
   | refuted, price drop | the argument's claim was contradicted, or its counter did not move (< 0.1 %) although the claim was verified or it made its final offer | odds × 1/3 |
   | ambiguous | anything else, including silence and the first counter (no pace to compare with) | unchanged |

   Every update is logged with its reason, and the model never writes a new probability.
5. **GUI:** the case view has a new **Hypotheses** tab per supplier. It shows:
   - move and expected reaction, agents and sources;
   - start and current probability;
   - the code check, with the reason when a hypothesis is not playable;
   - which hypothesis was played;
   - every update with its outcome and reason.

## Comparing it fairly

`scripts/hypothesis_ab.py` plays each simulator case twice per repeat on a fresh simulator, once with hypotheses off and once on.
- Both arms read the **same frozen research** (`HACKIT_BRIEF_CACHE`). The cache version is `alpha-nine-v2-hypotheses`, so old briefs without hypotheses are not reused.
- No other model call is made, so the hypothesis logic is the only difference between the arms.
- The simulator's bots ignore the text but react to term sheets, which is what a played trade changes.
- Output: `logs/hypothesis-ab/results.json` and a per-case table.

## Findings so far

- **Unit tests:** 10 new tests: cleaning, merging, update rules, bounds, engine play at no extra buyer cost, only tested hypotheses updated, and nothing changes when off. Full suite: 160 passed, 3 skipped, ruff clean.
- **Pipeline smoke test** on S01/S03 with **synthetic** frozen briefs (not research, labelled as such):
  - Both arms ended with the same score (S01 0.845, S03 0.761).
  - The played trades (payment 30, FCA) were not taken by the bots.
  - Both played hypotheses were judged on the first counter, so they stayed *ambiguous*.
  - Hypotheses whose term was already our plan were correctly marked not playable.
  - This shows the mechanics work; it says nothing about value.
- **First design correction:** "the counter falls by 1 %" was supported almost every round, because suppliers concede anyway. It now has to beat the supplier's own previous step.
- **Not run yet: real frozen research.** The shared model spend is EUR 42.42 against the `.env` cap of 30, so freezing research with the real model needs Burak to raise the cap. Command:
  `HACKIT_BUDGET_EUR=<cap> uv run python scripts/hypothesis_ab.py --cases S01 S02 S03 --repeats 2`

## Limits to keep in mind

- Most played trades come in round one, when the supplier's pace is unknown, so a `price_drop` hypothesis rarely gets a verdict early. `takes_term` is the more testable reaction.
- Claim results are matched to our citations in the order we made them. That order is the arena's claim order in the runs seen so far, but it is an assumption.
- Refusal detection uses a small, explicit keyword list per term. It misses unusual wording; a miss reads as ambiguous, never as a refutation.
