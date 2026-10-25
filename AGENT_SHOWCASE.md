# Agent handoff: HackIt Buyer Live + Showcase (branch `proposal-1-ui`)

Read this first if you continue this work. Owner: Burak (team Hack It). Chat with Burak in Turkish;
code, docs and the page are in English. Code freeze Sun 25 Oct 2026 13:00 CET, Live Final 13:30.

## What this branch is
- `proposal-1` (Jose, pushed from another PC) is the belief-based buyer in `buyer/`.
- `proposal-1-ui` = `proposal-1` + the watch UI ("HackIt Buyer Live") + the Showcase.
- **Sync rule:** whenever `origin/proposal-1` moves, merge it into `proposal-1-ui`
  (`git fetch origin && git merge origin/proposal-1`), run the gate, push. Never push to `proposal-1`.
  Our changes to Jose's files are only small telemetry hooks (`buyer/policy.py`, `buyer/runner.py`,
  `buyer/cli.py`), so conflicts should stay small; keep his logic, re-add the hooks.
- Checkout: `/home/burak/Belgeler/hackathon/tacto/hackit-p1-ui` (own clone; never edit `hackit-v2`).
- Mirror: Burak's own private repo https://github.com/Burak-GGM/team-hackit (remote `team`, branch
  `main` = `proposal-1-ui`). After every push of `proposal-1-ui`, also `git push team proposal-1-ui:main`.
- Gate: `.venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .`

## Files
| Path | Role |
|---|---|
| `buyer/telemetry.py` | passive live log `logs/live/case_<id>.jsonl` (notes, e-mails, model calls, result); `--no-live` off |
| `buyer/liveview.py` | stdlib HTTP server: `/api/cases`, `/api/events`, `/api/scores`, `/api/history`, `/api/run`, `/api/showcase` |
| `buyer/liveview.html` | the single page (vanilla JS, no external assets) |
| `buyer/showcase.py` | turns one recorded run into a storyboard (steps with captions) for the Showcase |
| `buyer/showcase/*.jsonl` | bundled real runs (frozen live logs) so the Showcase works with no arena and no logs |
| `docs/liveview-contract.md` | API contract of the watch UI |
| `scripts/sim_demo.py` | local arena-sim runs for UI testing (no arena, no quota, no memory writes) |

Commands: `buyer gui [--port N] [--offline]`, `--gui` / `--no-live` on play/practice/sandbox/final,
`buyer showcase --list`, `buyer showcase --save <session>` (freeze a recorded run into `buyer/showcase/`).

## Showcase (the jury demo)
A **Showcase** button in the header opens a full-screen, auto-playing walkthrough of one real,
recorded buyer interaction (a successful one by default), step by step, with animations: the case,
the preparation per supplier, every e-mail out and in, the belief narrowing, the planner's candidate
table behind each price, the guards that fired, the close and the result. It must work after the
Live Final and with no network: it falls back to the bundled runs in `buyer/showcase/`.
A selector lets the presenter pick another recorded run (successful or not).

### GET /api/showcase[?session=ID]  -> storyboard
```
{ source: "bundled"|"recorded", session, case_id, title, type, goal, set,
  result: {deal, supplier_id, score, c_open, c_deal, c_alt, c_best, end_reason},
  suppliers: [{id, q0, anchor, prior: [mean, sd], signals, owner, rate, full_cost, claims, terms, std}],
  rounds: {sid: [{round, ours, theirs, status, p10, p50, p90}]},      # EUR, for the charts
  choices: [{session, case_id, title, score, deal, source}],           # what the selector offers
  steps: [ {kind, title, caption, supplier?, round?, flow: [[from, to], ...], data: {...}} ] }
```
Step kinds, in order: `intro` (data.case_title, type, goal, suppliers, c_alt, need, horizon), `prep` (one per supplier: profile,
reader labels with quotes, claims, planned terms, anchor, prior; memory shift), `send` (our e-mail:
subject, body, term sheets, claims), `reply` (its e-mail: status, price, claim checks; belief after),
`plan` (planner table: candidates with expected C, P(walk), rounds; chosen price), `guard` (a notable
rule that fired: deep-drop hold, probe package, learned rate, refused term, mirror, dominated bidder,
ceiling), `close` (accept / walk away), `result` (C open vs C deal vs alternative, score), `outro`
(the principles: code owns every number, belief + planner, guards). `flow` names module edges of the
system map (ids in docs/liveview-contract.md) to animate a data packet along.

## Status (update as you go)
- [x] proposal-1-ui branch, telemetry, watch UI backend + page, tests, pushed
- [x] checked: nothing of `proposal-1` / `belief-buyer` missing (belief-buyer is an older proposal-1)
- [x] old hackit2 UI features: ported except hackit2-only ones (nine-agent research, buyer boundaries,
      observer sessions, model spend, pulled arena sessions) - this agent has none of them
- [x] rename brand to "HackIt Buyer Live" (page title, header, server print, README)
- [x] `buyer/showcase.py` storyboard + `/api/showcase` + `buyer showcase` CLI + tests (`tests/test_showcase.py`)
- [x] bundled real runs: `buyer/showcase/default.jsonl` (D01, deal, 0.940), `rfq-d02.jsonl` (D02, deal, 0.723); sample storyboard `docs/showcase-sample.json`
- [x] Showcase player in `buyer/liveview.html` (Codex): overlay, auto-play, captions, animated packets
      on the map, per-kind visuals, play/pause/prev/next/speed/exit, keys Space/←/→/Esc, run selector
- [x] browser check (1280x720: D01 all 15 steps, D02 selector), gate, push
- [x] merged proposal-1 bd2be13 (supplier dossier); conflict only in the telemetry hooks of policy.py
- [ ] optional: after the Live Final, `buyer showcase --list` and `--save <best final session> --name final`
- [ ] optional polish: hide the empty "Alternative" bar when a case has no alternative (D01)

## Rules
- Never spend practice quota without Burak's OK (sandbox D01/D02 and arena-sim are free).
- Live Final: the agent and the UI must not call /v1/events, /screen, /leaderboard, /results;
  `buyer final --gui` makes no arena call from the UI at all.
- "Hollerau Pumpenwerke" is the buyer company of the cases: keep it in e-mails and prompts; only the
  UI brand is "HackIt Buyer".
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
