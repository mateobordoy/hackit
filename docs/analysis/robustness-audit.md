# Robustness audit: what in hackit2 is generic, what is fitted to the practice bots

Branch `audit` (from main e9c1890). Analysis only: no code change, no practice session, no model call.
Data: 308 hosted practice sessions (`logs/sessions/practice/*.json`, 10 cases, every one with the
supplier's back office and the bot's reasons per move) and 102 run journals (`hackit-*/logs/runs`).

Why: the Live Final has new suppliers and settings, with the same world, case types and rules (brief
section 8). Constants fitted to the practice suppliers may not carry over. What the supplier engine
does for every supplier probably does carry over.

## 1. Which arguments move suppliers (bot reasons, all 10 cases)

For every claim we cite, the supplier answers with a templated reason per claim type: "it holds and we
took it into account", "it holds, but it does not change our calculation", "we did not know that - a
fair point", "that is not our reality", "does not concern this part". Counted over all sessions:

| Claim type (our topic, weight) | Took / did not know | No change | Moved share |
|---|---|---|---|
| price_history (`order_history`, weight 4) | 0 / 6 | 188 | **3 %** |
| delivery_record (`scorecard`, 2) | 74 / 17 | 137 | 40 % |
| volume_change (`forecast`, 2) | 146 / 8 | 61 | 70 % |
| rival_quote (`rival`, 6-7) | 68 / 4 | 26 | 71 % |
| should_cost (`should_cost`, 5) | 59 / 10 | 21 | 77 % |
| index_change (`index`, 4-7) | 121 / 17 | 17 (+6 not reality) | 81 % |
| notice_period (`notice`, 5) | 24 / 16 | 0 | 98 % |
| quality_record (`scorecard`, quality cases) | 24 / 0 | 0 | 100 % |

- **Order history almost never moves a supplier** (0 of 194 "took it into account"), across every
  case and supplier. It looks like a rule of the supplier engine: past prices are not costs. Yet it
  ranks above forecast and scorecard (weight 4 vs 2). We cite it in round 1 in 161 of 405 first
  messages, using one of the three round-1 slots.
- Accepted arguments do pay. Supplier's first price drop by the number of arguments it took (round
  1, all cases mixed, so it is confounded by case):

  | Accepted | Replies | Median drop | Mean drop |
  |---|---|---|---|
  | 0 | 108 | 0.97 % | 2.21 % |
  | 1 | 153 | 1.70 % | 4.45 % |
  | 2 | 55 | 1.55 % | 3.29 % |
  | 3 | 39 | 3.50 % | 6.95 % |
  | 4 | 19 | 16.7 % | 10.8 % |

- **Candidate (generic, small):** rank order history last (weight 1, below forecast and scorecard),
  so the round-1 slot goes to an argument that can move the supplier. Risk is low: it only changes
  which verified fact we cite, never a price. It needs a practice A/B, because it changes behaviour.

## 2. What suppliers ask for, and the approval ladder

- **"Which volume can you commit to?"** is the most frequent bot line (368 times; "a binding higher
  volume would help us" 305). We answered it in the next message 174 times (121 in text, 53 with a
  volume field on a sheet) and ignored it 194 times. Its next price drop is the same either way
  (median 0.36 % text, 0.32 % ignored, 0.00 % on a sheet). We can only confirm our full demand, never
  more, so this is no lever. No change.
- **Approval ladder** ("We need to clarify this internally - we will come back to you on price"):
  only P05, both bidders, in all 33 sessions. Our gap when it fires: median 2.3 %. The next reply
  moves a median 4.7 % and says "clarified ... can move further" 96 % of the time. The engine
  already waits it out (P05 median 0.82). Generic rule worth keeping: a "clarify internally" is not
  a final word.

## 3. When suppliers stop: final offer and walk-away

- Most of our deals close on the supplier's **final_offer** (P03, P04, P07, P08, P10, P11, P18), so
  its final-offer level sets our score. Cumulative relative gap (sum over rounds of (their counter -
  our sheet) / their counter) when the final offer comes:

  | Case | n | Cum. gap at final offer (median) | Round (median) |
  |---|---|---|---|
  | P01 | 5 | 0.144 | 5 |
  | P02 | 6 | 0.134 | 4 |
  | P03 | 14 | 0.089 | 6 |
  | P04 | 27 | 0.079 | 3 |
  | P05 | 12 | 0.402 | 4 |
  | P07 | 22 | 0.287 | 3 |
  | P08 | 31 | 0.251 | 3 |
  | P10 | 35 | 0.067 | 2 |
  | P11 | 21 | 0.289 | 5 |
  | P18 | 25 | 0.156 | 4 |

  There is no single patience threshold: the final offer comes at 7-40 % cumulative gap and in round
  2-6. The engine's patience constants (default 1.0, relationship 0.3, fully booked 0.16, near cost
  0.14) are fitted guesses per persona. For new suppliers the reliable signal is the supplier's own
  word (`final_offer` status), which the engine already obeys (rule 5). Since P10 says final in round
  2, **round 1 carries most of the value**: what we cite in round 1 (section 1) matters more than the
  pace constants.
- Failures in the 308 sessions (score 0 or < 0.3): 14, all from experimental builds or old code:
  - 5 x three buyer timeouts in a row (Sat 21:00-21:52, Sun 02:16: the agent never sent a message);
  - P02 Sun 02:58: opened at 351 without the index clause (the reverted T18 build), the supplier
    raised its price and walked in round 4;
  - P18 Sun 02:16: opened at 0.95 of the quote (not main's 0.78), so the supplier agreed at once.
  The hosted sessions do not record the build. Main's own failure rate cannot be told apart; the
  two recent main runs (P08, P10) closed normally.
