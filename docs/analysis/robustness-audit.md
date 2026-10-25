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
