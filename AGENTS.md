# AGENTS.md - read this first (every AI agent and every teammate)

Team **Hack It** (Burak, Andrey, Jose, Mateo), Industrial Agents Hackathon 2026, Munich, Tacto
Negotiation Arena track. Several AI agents work on this repo **at the same time**: opencode (Claude
API), Claude Code (Claude subscription), Codex and agy (Gemini 4). This file is the shared contract;
`CLAUDE.md` and `GEMINI.md` only point here.

The human lead is **Burak**. He writes in Turkish: answer him in Turkish. Code, comments, commit
messages and docs are in **English**.

## 1. The task in five lines

- `hackit2` is an autonomous buyer for the fictional pump maker Hollerau Pumpenwerke. It negotiates by
  e-mail (arena API) with Tacto's supplier bots and accepts the term sheet with the lowest total cost C,
  or walks away when the alternative is cheaper.
- Score per case = `clip((C_open - C_deal) / (C_open - C_best), 0, 1)`. C is computed exactly by the
  kit's `arena_kit.cost.total_cost`. Breaking a must-have scores 0.
- **Live Final: Sun 25 Oct 2026, 13:30 CET.** 5 waves x 8 minutes, hidden cases with new suppliers,
  one attempt per case, **no human in the loop**. **Code freeze 13:00 CET**: the submitted commit is the
  one that runs. Data release at **08:00 CET** (`uv run arena data`).
- Rules: `../negotiation-arena-kit/brief/brief.md`, `../negotiation-arena-kit/brief/api.md`.

## 2. Where we are now (update this section when the state changes)

- `hackit2` is a rewritten and restructured derivative of last edition's winner ProcureCabana, reused
  under the hackathon's open-code terms (credit in `NOTICE`; never remove it, never hide the origin).
  Rule of the design: **code owns every number** (prices, terms, accept/walk); models only advise
  (clamped to fixed lists) and write one paragraph per e-mail.
- Practice results (10 hosted cases, same setup as the original): run 1 **76.5**, run 2 **77.6**;
  original **78.5**. Rounds 1 are identical in every case: the gap is LLM/bot variance, mostly P08.
  Details: `logs/compare/digest.md` and `logs/compare/chats.html` (local only).
- Known weak spots (tasks T2, T3, T9 on the board):
  - P08: the reply reader's `hold` tactic slowed round 2, the supplier ran out of patience one round
    earlier (0.538 vs 0.737).
  - P11 run 2: a prepared trade loosened `ppm_commitment` (300 -> 800); the supplier still stopped at
    the same price floor, so the looser term just cost us money (-0.038).
  - P02, P18 (~0.63-0.73) are the hardest cases for both agents.
- Done today: rewrite, 73 unit tests green, live telemetry + watch UI (`--gui`, `--no-gui`,
  `hackit2 gui`), side-by-side comparison with the original.
- Team quota: practice sessions are scarce (limit 400 / rolling 24 h, ~270 left at 23:00 Sat). Model
  budget: EUR 30 hard cap, ~EUR 13.5 spent; shared spend file (see section 5).

The live task board is **`../agents-hub/BOARD.md`** (generated). Read it before you start.

## 3. Hard rules (breaking one can disqualify us or lose the final)

1. **Secrets.** Never open, print, copy or commit `.env` or any key (`ARENA_API_KEY`,
   `ANTHROPIC_API_KEY`). Only `.env.example` holds names.
2. **Practice quota.** `hackit2 play case:P..` / `hackit2 run --set practice` spend the team quota. Never
   start one without Burak's explicit OK **and** the `@arena-practice` claim (section 4). Free without
   asking: unit tests, the local simulator (`arena-sim`), the sandbox (`case:D01`, `case:D02`).
3. **Live Final.** Agent code never calls `/v1/events`, `/v1/events/stream`, `/screen`, `/v1/screen`,
   `/leaderboard`, `/v1/leaderboard`, `/results`, `/v1/results`. The final runs headless:
   `uv run hackit2 run --set final --no-gui`. No pushes to `burak` after the code freeze.
4. **Code owns every number.** Never let a model set a price, a term, an accept or a walk-away. Model
   text with digits or data IDs is dropped (`model.UNSAFE_TEXT`). Keep it that way.
5. **Never push after a final offer.** After `final_offer` / `agreed` only accept, or echo back a sheet
   the supplier itself offered.
6. **A bad deal beats no deal.** Never add a path that walks away while an acceptable sheet stands.
7. **Don't edit Tacto's kit** (`../negotiation-arena-kit`). Wrap it.
8. **Performance first.** A change to `hackit2/negotiation/`, `pricing.py`, `evidence.py`, prompts or
   `settings.py` needs evidence (simulator/sandbox, or practice with Burak's OK) and a test. One run is a
   data point, not a law.
9. **Tests green before any merge:** `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

## 4. Working in parallel: worktrees, claims, merges

### 4.1 Each agent has its own worktree and branch

| Agent | Tool | Worktree (absolute path) | Branch |
|---|---|---|---|
| opencode | opencode, Claude via API | `/home/burak/Belgeler/hackathon/tacto/hackit-opencode` | `agent/opencode` |
| claude | Claude Code (subscription) | `/home/burak/Belgeler/hackathon/tacto/hackit-claude` | `agent/claude` |
| codex | Codex CLI | `/home/burak/Belgeler/hackathon/tacto/hackit-codex` | `agent/codex` |
| agy | agy, Gemini 4 | `/home/burak/Belgeler/hackathon/tacto/hackit-agy` | `agent/agy` |
| integration | Burak | `/home/burak/Belgeler/hackathon/tacto/hackit-v2` | `burak` |

- Work **only** inside your own worktree. Never edit files in another agent's worktree or in
  `hackit-v2` (the integration tree, branch `burak`, which pulls finished work).
- Each worktree has its own `.venv` (`uv sync`) and a symlink to the shared `.env`.

### 4.2 Claim before you edit (the hub)

The hub lives outside git so every agent sees the same state:
`/home/burak/Belgeler/hackathon/tacto/agents-hub/` (`hub.py`, `BOARD.md`, `journal.md`, `handoffs/`).

```sh
HUB="python3 /home/burak/Belgeler/hackathon/tacto/agents-hub/hub.py"
$HUB status                                        # board, active claims, latest journal lines
$HUB task take T3 codex                            # one task at a time
$HUB claim codex T3 hackit2/negotiation/packages.py tests/test_engine_packages.py --why "trade safety"
$HUB check hackit2/pricing.py                      # who holds it?
$HUB note codex "T3: guard added, 2 tests, PR-ready on agent/codex"
$HUB task done T3 codex --note "merged as abc1234"
$HUB release codex                                 # always release when done or when you stop
```

Rules:
- **No edit without a claim** on every file or folder you will touch, tests included. The hub refuses
  overlapping claims (same file, a file inside a claimed folder, a matching glob).
- Claim narrowly (files, not `hackit2/`). Claims expire after 3 h (`--ttl` minutes to change); renew
  by claiming again.
- Need a file someone else holds? Do not touch it. Write a journal note asking for it, or pick another
  task.
- Special resources: `@arena-practice` (any practice session; plus Burak's OK) and `@merge-burak`
  (pushing to `burak`). Claim them like files: `$HUB claim codex T3 @merge-burak --ttl 10`.
- Tools without a shell (e.g. agy in print mode): ask Burak to run the hub commands for you, and say
  which paths you need **before** you write anything.

### 4.3 Getting work into `burak`

```sh
git fetch origin && git rebase origin/burak               # in your worktree, on agent/<you>
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
$HUB claim <you> <task> @merge-burak --ttl 10
git push origin HEAD:burak                                # fast-forward only; rejected -> fetch, rebase, retest
git push -f origin HEAD:agent/<you>                       # optional backup of your branch
$HUB release <you> @merge-burak && $HUB task done <task> <you> --note "<commit>"
git -C /home/burak/Belgeler/hackathon/tacto/hackit-v2 pull --ff-only   # refresh the integration tree
```

- Small commits, one task per commit series, message `T<n>: <what and why>`.
- Never force-push `burak`, never rewrite its history, never commit `.env`, `logs/` or `index.html`.
- Before you stop: commit or stash, write `handoffs/<you>.md` in the hub (what is done, what is open,
  what you learned), `hub.py note`, release your claims.

## 5. Setup and commands

```sh
uv sync                                             # Python 3.12; kit pinned in pyproject.toml
uv run pytest -q                                    # unit tests, local simulator, no quota
uv run hackit2 play case:D01 -v                     # sandbox, free (model costs a few cents)
uv run hackit2 play case:D01 --no-llm -v            # sandbox without the model, free
uv run hackit2 play case:P01 -v                     # practice: quota! Burak's OK + @arena-practice
uv run hackit2 run --set practice --parallel 3 -v   # all 10 hosted practice cases (quota!)
uv run hackit2 run --set final --no-gui             # the Live Final, headless
uv run hackit2 gui                                  # watch UI, http://127.0.0.1:8790 (or --gui on play/run)
uv run hackit2 pull --set practice                  # finished sessions into logs/sessions/
```

- `.env` keys (names only): `ARENA_URL`, `ARENA_API_KEY`, `ARENA_DATA` (`../package`), `LLM_PROVIDER`,
  `LLM_MODEL`, `LLM_EFFORT`, `ANTHROPIC_API_KEY`, `HACKIT_BUDGET_EUR`, `HACKIT_SPEND_FILE` (absolute,
  shared by all worktrees so the EUR cap is global), `HACKIT_NO_GUI`.
- Local simulator (free), from `../negotiation-arena-kit/starter-kit`:
  `uv run arena-sim serve --port 8766 --data ../../package --final-rehearsal`, then
  `ARENA_URL=http://127.0.0.1:8766 uv run arena register "name" sim` and run against it.
- Run two agents' live tests on different watch-UI ports: `--gui --gui-port 8791`.

## 6. Code map

| Path | Role |
|---|---|
| `hackit2/settings.py` | every tunable number (read as `settings.X` at call time) |
| `hackit2/session.py` | case loop: clocks, one message at a time, keepalives, close, batches, Live Final waves |
| `hackit2/negotiation/engine.py` | `Negotiator`: open a thread, each move, the e-mail, delivery bookkeeping |
| `hackit2/negotiation/{concession,packages,closing,thread,common}.py` | price step, alternative sheets, echo/accept/walk, per-thread state |
| `hackit2/pricing.py` | C, supplier worth, term planning, price ceiling, relationship sweet spot |
| `hackit2/evidence.py` | citable arguments in the exact form the arena verifies |
| `hackit2/supplier_profile.py` | ownership, traits, cost of money from the data package |
| `hackit2/prep/briefing.py`, `prep/sparring.py` | preparation team (4 specialists, lead, sparring) before the first e-mail |
| `hackit2/reply_reader.py`, `mailer.py`, `transcript.py` | reading replies, writing e-mails, conversation evidence |
| `hackit2/model.py` | LLM transports (Anthropic with spend cap, OpenAI-compatible), timeouts, the text guard |
| `hackit2/lessons.py`, `lessons.jsonl` | cross-case memory (committed, the final starts with it) |
| `hackit2/telemetry.py`, `liveview.py`, `liveview.html` | live telemetry and the watch UI |
| `hackit2/cli.py` | `hackit2 play | run | pull | gui` |
| `tests/` | unit + simulator tests; `conftest.py` sends live files to a temp folder |

## 7. Conventions

- Small, focused functions; constants in `settings.py` with a comment saying why (cite the case).
- Comments explain what and why, in plain English. No new dependencies without a reason.
- Every behaviour change gets a test. Log decisions through `Negotiator.log_event`.
- After a run, read the replay or `logs/compare/chats.html`, not just the score.
