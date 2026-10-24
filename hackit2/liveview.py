"""hackit2 Live: a local watch UI for running (and finished) cases.

Reads the live files the agent writes (`logs/live/case_<id>.jsonl`, see `telemetry.py`) and
serves a single page that shows, per case: the module map with the code paths running right now and
the data flowing between them, the preparation team's thinking, every model call with prompt and
answer, the e-mail thread with term sheets and claim checks, the price paths and the decision trace.

The Scores view compares the latest run of each case (from every worktree's `logs/runs/`) with the
earlier runs, the team's best and the reference run. The History view lists every recorded run of
every worktree (live files, run journals and the arena sessions pulled to `logs/sessions/`) and opens
any of them in the case view, also when nothing runs. The team's best and the sessions' start times
come from the pulled sessions and, unless `offline`, from the arena's `GET /v1/me` and
`GET /v1/sessions` once a minute: the only calls this UI makes to the arena (never the endpoints the
Live Final forbids). Standard library only.

    uv run hackit2 gui                          # http://127.0.0.1:8790, watch any run
    uv run hackit2 gui --offline                # the same, never contacting the arena
    uv run hackit2 run --set practice --gui     # the agent and the UI in one process
    uv run hackit2 run --set final --no-gui     # headless: no UI and no live files at all
"""

import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# roots: the checkouts whose logs the Scores view reads (None: this one and its sibling worktrees);
# reference: the file with the reference run's scores (None: logs/compare/digest.md)
CONFIG = {
    "live": Path("logs/live"),
    "spend": None,
    "arena": "https://arena.tacto.ai",
    "roots": None,
    "offline": False,
    "reference": None,
}
TEAM_REFRESH_S = 60  # how often the team's best is read from the arena
_cache = {}
_json_cache = {}
_team = {"best": {}, "practice": {}, "at": None, "error": None, "created": {}}


def read_events(path):
    """All JSON lines of a live file, cached by size and modification time."""
    stat = path.stat()
    key = (stat.st_size, stat.st_mtime_ns)
    cached = _cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    events = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    _cache[path] = (key, events)
    return events


def latest_run(events):
    """The events of the most recent run in a file: from its last `case_start` (or `session`) on."""
    for marker in ("case_start", "session"):
        starts = [i for i, e in enumerate(events) if e.get("event") == marker]
        if starts:
            return events[starts[-1] :]
    return events


def case_summary(path):
    run = latest_run(read_events(path))
    head = next((e for e in run if e.get("event") == "case_start"), {})
    session = next((e for e in run if e.get("event") == "session"), {})
    end = next((e for e in reversed(run) if e.get("event") == "end"), None)
    suppliers = session.get("suppliers") or sorted({e["supplier"] for e in run if e.get("supplier")})
    last = run[-1].get("at") if run else 0
    return {
        "case": path.stem.replace("case_", ""),
        "title": head.get("title") or session.get("title"),
        "type": head.get("type") or session.get("type"),
        "goal": head.get("goal") or session.get("goal"),
        "status": "done" if end else ("live" if time.time() - (last or 0) < 180 else "stalled"),
        "score": end.get("score") if end else None,
        "deal": end.get("deal") if end else None,
        "session_id": session.get("session_id"),
        "started": run[0].get("at") if run else 0,
        "last": last,
        "suppliers": suppliers,
        # an observer (a sidecar that only polls the arena) cannot see our model calls: unknown, not 0
        "model_calls": None if head.get("observer") else sum(1 for e in run if e.get("event") == "model_call"),
        "observer": bool(head.get("observer")),
        "trial": head.get("trial"),
        "events": len(run),
    }


def live_files():
    """{case key: (path, source)} for every live file: this UI's own folder (key "P05") and the
    `logs/live` folders of the sibling worktrees (key "P05@codex"), so a run started in any worktree
    shows up while it runs."""
    files, own = {}, CONFIG["live"]
    here = source_name(Path.cwd().resolve())
    if own.is_dir():
        for path in own.glob("case_*.jsonl"):
            files[path.stem.replace("case_", "")] = (path, here)
    own_folder = own.resolve() if own.is_dir() else None
    for root in CONFIG["roots"] or checkouts():
        folder = root / "logs" / "live"
        if not folder.is_dir() or folder.resolve() == own_folder:
            continue
        for path in folder.glob("case_*.jsonl"):
            files[f"{path.stem.replace('case_', '')}@{source_name(root)}"] = (path, source_name(root))
    return files


def all_cases():
    cases = [{**case_summary(path), "case": key, "source": source} for key, (path, source) in live_files().items()]
    return sorted(cases, key=lambda c: (c["status"] != "live", -(c["last"] or 0)))


def running_sessions():
    """The team's arena sessions that are still open, from the session list read once a minute
    (`read_team_best`); {} offline."""
    return {sid: s for sid, s in _team.get("sessions", {}).items() if s.get("status") not in ("ended", None)}


def case_events(case, start):
    path = (live_files().get(case) or (CONFIG["live"] / f"case_{case}.jsonl", None))[0]
    if not path.is_file():
        return {"events": [], "next": 0, "run_start": None}
    run = latest_run(read_events(path))
    run_start = run[0].get("at") if run else None
    return {"events": run[start:], "next": len(run), "run_start": run_start}


def spend():
    path = CONFIG["spend"]
    try:
        return json.loads(Path(path).read_text()) if path else {}
    except (OSError, ValueError):
        return {}


# --- Scores: the latest runs against earlier runs, the team's best and the reference run ---------

REFERENCE_ROW = re.compile(r"^\|\s*([PD]\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|")


def checkouts():
    """This checkout and its sibling worktrees (`hackit-v2`, `hackit-<agent>`), so the runs of every
    agent show up side by side."""
    here = Path.cwd().resolve()
    siblings = [p.resolve() for p in sorted(here.parent.glob("hackit-*")) if (p / "logs").is_dir()]
    return [here] + [p for p in siblings if p != here]


def source_name(root):
    """'claude' for hackit-claude, 'v2' for the integration tree hackit-v2."""
    return root.name.removeprefix("hackit-")


def short_case(case_id):
    return str(case_id).split(":")[-1]


def read_json(path):
    """A JSON file, cached by size and modification time (pulled sessions are large); None if unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (stat.st_size, stat.st_mtime_ns)
    cached = _json_cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = None
    _json_cache[path] = (key, value)
    return value


def finished_runs(roots):
    """Every finished run in the checkouts' `logs/runs/`, oldest first: case, session, score, deal,
    when it ended (the file's time) and which checkout played it. A session found twice counts once."""
    runs, seen = [], set()
    for root in roots:
        for path in (root / "logs" / "runs").glob("*.json"):
            record = read_json(path)
            if not isinstance(record, dict) or not record.get("case"):
                continue
            session = record.get("session") or path.stem
            if session in seen:
                continue
            seen.add(session)
            result = record.get("result") or {}
            runs.append(
                {
                    "case": short_case(record["case"]),
                    "session": session,
                    "score": result.get("score"),
                    "deal": result.get("deal"),
                    "at": path.stat().st_mtime,
                    "source": source_name(root),
                }
            )
    return sorted(runs, key=lambda run: run["at"])


def pulled_best(roots):
    """The team's best score per case among the sessions pulled to `logs/sessions/` (`hackit2 pull`):
    every ended session of the team's key, whichever agent or teammate played it."""
    best = {}
    for root in roots:
        for path in (root / "logs" / "sessions").glob("*/*.json"):
            record = read_json(path)
            score = ((record or {}).get("result") or {}).get("score") if isinstance(record, dict) else None
            if score is None or not record.get("case_id"):
                continue
            case = short_case(record["case_id"])
            best[case] = max(best.get(case, score), score)
    return best


def read_reference(path):
    """{case: score} from a run manifest (`.json`: `results[]` with `case_id` and `score`) or a
    comparison digest (`.md`: the third column of its table); {} when unreadable."""
    if path.suffix == ".json":
        record = read_json(path)
        results = record.get("results") if isinstance(record, dict) else record
        return {
            short_case(r["case_id"]): float(r["score"])
            for r in results or []
            if isinstance(r, dict) and r.get("case_id") and r.get("score") is not None
        }
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    rows = (REFERENCE_ROW.match(line) for line in text.splitlines())
    return {m.group(1): float(m.group(3)) for m in rows if m}


def reference_scores(roots):
    """The reference run's score per case: from the configured file (`--reference` or
    HACKIT_REFERENCE_FILE), else from `logs/compare/digest.md` in the checkouts."""
    configured = CONFIG.get("reference")
    paths = [Path(configured)] if configured else [root / "logs" / "compare" / "digest.md" for root in roots]
    for path in paths:
        found = read_reference(path)
        if found:
            return found
    return {}


def case_order(case):
    """Practice cases first, then the sandbox, each in number order."""
    return (not case.startswith("P"), int(re.sub(r"\D", "", case) or 0), case)


def board_total(rows, field):
    """Like the practice board: the mean over the practice cases × 100, a case without a score
    counting 0. None when no practice case has this score."""
    practice = [row for row in rows if row["case"].startswith("P")]
    values = [row[field] for row in practice]
    if not practice or all(v is None for v in values):
        return None
    return round(100 * sum(v or 0 for v in values) / len(practice), 1)


def scores():
    """Per case: the latest run, the run before it and the change, our best run, the team's best
    (pulled sessions and the arena's live board, whichever is higher) and the gap to it, the
    reference run, and the last runs as a history; plus board-style totals."""
    roots = CONFIG["roots"] or checkouts()
    runs = finished_runs(roots)
    pulled, live, reference = pulled_best(roots), dict(_team["best"]), reference_scores(roots)
    cases = sorted({run["case"] for run in runs} | set(pulled) | set(live) | set(reference), key=case_order)
    rows = []
    for case in cases:
        scored = [run for run in runs if run["case"] == case and run["score"] is not None]
        latest = scored[-1] if scored else None
        previous = scored[-2] if len(scored) > 1 else None
        team = max((v for v in (pulled.get(case), live.get(case)) if v is not None), default=None)
        rows.append(
            {
                "case": case,
                "latest": latest,
                "previous": previous,
                "change": latest["score"] - previous["score"] if previous else None,
                "best": max((run["score"] for run in scored), default=None),
                "team": team,
                "team_pulled": pulled.get(case),
                "team_live": live.get(case),
                "gap": latest["score"] - team if latest and team is not None else None,
                "reference": reference.get(case),
                "history": scored[-12:],
            }
        )
    for row in rows:
        row["latest_score"] = row["latest"]["score"] if row["latest"] else None
    totals = {f: board_total(rows, f) for f in ("latest_score", "best", "team", "reference")}
    return {
        "rows": rows,
        "totals": totals,
        "team": {
            **{k: v for k, v in _team.items() if k != "created"},
            "offline": CONFIG["offline"],
            "pulled_cases": len(pulled),
        },
        "sources": sorted({run["source"] for run in runs}),
        "now": time.time(),
    }


def read_team_best():
    """The team's best score per practice case and its quota from `GET /v1/me`, and when each of the
    team's practice and sandbox sessions started (`GET /v1/sessions`, for the History view), into
    `_team`."""
    try:
        from arena_kit.client import ArenaClient

        client = ArenaClient()
        me = client.me()
        best = {short_case(k): v for k, v in (me.get("best") or {}).items() if v is not None}
        _team.update(best=best, practice=me.get("practice") or {}, at=time.time(), error=None)
        created, sessions = _team.setdefault("created", {}), {}
        for case_set in ("practice", "sandbox"):
            for item in client.sessions(case_set) or []:
                if item.get("session_id") and item.get("created_at"):
                    created[item["session_id"]] = item["created_at"]
                if item.get("session_id"):
                    sessions[item["session_id"]] = {**item, "set": case_set}
        _team["sessions"] = sessions
    except Exception as exc:  # noqa: BLE001 - no network or key: the view shows the pulled sessions only
        _team["error"] = str(exc)[:160]


def team_loop():
    while not CONFIG["offline"]:
        read_team_best()
        time.sleep(TEAM_REFRESH_S)


# --- History: every recorded run, from live files, run journals and pulled arena sessions --------

CHAT_EVENTS = ("message_in", "message_out")


def live_segments(events):
    """The runs inside one live file, oldest first: lists of events, each starting at a `case_start`,
    or at a `session` in older files that have no `case_start`."""
    segments, has_session = [], False
    for event in events:
        kind = event.get("event")
        if not segments or kind == "case_start" or (kind == "session" and has_session):
            segments.append([])
            has_session = False
        segments[-1].append(event)
        has_session = has_session or kind == "session"
    return segments


def recorded_runs(roots):
    """{session: record} for every run recorded in the checkouts, its three sources merged by session
    id: the live-file segment (`live`: path and index; the full telemetry in newer runs), the run
    journal (`journal`: our decisions) and the pulled arena session (`arena`: both sides' e-mails, the
    supplier's back office, the result), with case, set, start time, score, deal and who played it."""
    runs = {}

    def entry(session, case):
        blank = dict.fromkeys(("set", "at", "score", "deal", "source", "title", "type", "goal"))
        return runs.setdefault(
            session, {"session": session, "case": case, **blank, "live": None, "journal": None, "arena": None}
        )

    for root in roots:
        for path in sorted((root / "logs" / "live").glob("case_*.jsonl")):
            for index, segment in enumerate(live_segments(read_events(path))):
                head = next((e for e in segment if e.get("event") == "case_start"), {})
                opened = next((e for e in segment if e.get("event") == "session"), {})
                end = next((e for e in reversed(segment) if e.get("event") == "end"), None)
                session = opened.get("session_id") or f"live:{source_name(root)}:{path.stem}:{index}"
                run = entry(session, path.stem.replace("case_", ""))
                run.update(live=(str(path), index), source=run["source"] or source_name(root), at=segment[0].get("at"))
                for key in ("title", "type", "goal"):
                    run[key] = head.get(key) or opened.get(key) or run[key]
                if end:
                    run.update(score=end.get("score"), deal=end.get("deal"))
        for path in (root / "logs" / "runs").glob("*.json"):
            record = read_json(path)
            if not isinstance(record, dict) or not record.get("case"):
                continue
            run = entry(record.get("session") or path.stem, short_case(record["case"]))
            result, journal = record.get("result") or {}, record.get("journal") or []
            run.update(journal=str(path), source=run["source"] or source_name(root))
            if run["score"] is None:
                run.update(score=result.get("score"), deal=result.get("deal"))
            if run["at"] is None and journal and isinstance(journal[0], dict):
                run["at"] = journal[0].get("at")
        for path in (root / "logs" / "sessions").glob("*/*.json"):
            record = read_json(path)
            if not isinstance(record, dict) or not record.get("session_id"):
                continue
            run = entry(record["session_id"], short_case(record.get("case_id")))
            result = record.get("result") or {}
            run.update(arena=str(path), set=record.get("set") or path.parent.name)
            if run["score"] is None:
                run.update(score=result.get("score"), deal=result.get("deal"))
            run["at"] = run["at"] or record.get("created_at") or _team.get("created", {}).get(record["session_id"])
    titles = {run["case"]: (run["title"], run["type"], run["goal"]) for run in runs.values() if run["title"]}
    for run in runs.values():
        if not run["set"]:
            run["set"] = {"P": "practice", "D": "sandbox"}.get(run["case"][:1], "other")
        if not run["title"] and run["case"] in titles:
            run["title"], run["type"], run["goal"] = titles[run["case"]]
        run["source"] = run["source"] or "team"  # pulled from the arena: any teammate or agent
    return runs


def run_summary(run):
    """A recorded run without its file paths, plus which sources hold it."""
    public = {k: v for k, v in run.items() if k not in ("live", "journal", "arena")}
    return {
        **public,
        "has_live": run["live"] is not None,
        "has_journal": run["journal"] is not None,
        "has_arena": run["arena"] is not None,
    }


def history():
    """Every recorded run, newest first (runs without a known start time last)."""
    runs = recorded_runs(CONFIG["roots"] or checkouts())
    for session, item in running_sessions().items():  # open on the arena: running now, maybe in no local file
        run = runs.get(session)
        if run is None:
            case = short_case(item.get("case_id"))
            blank = dict.fromkeys(("title", "type", "goal", "score", "deal"))
            run = runs[session] = {
                "session": session,
                "case": case,
                **blank,
                "live": None,
                "journal": None,
                "arena": None,
            }
            run.update(set=item.get("set"), at=item.get("created_at"), source="team")
        run["running"] = True
    ordered = sorted(runs.values(), key=lambda run: (not run.get("running"), run["at"] is None, -(run["at"] or 0)))
    return {"runs": [run_summary(run) for run in ordered], "offline": CONFIG["offline"], "now": time.time()}


def arena_events(state, known, with_messages):
    """A pulled arena session as the case view's events: each e-mail both ways with its term sheets
    and claim checks, our timeouts, the price paths, the supplier's back office (its departments'
    internal notes) and the bot's reasons, and the result with its cost breakdown.

    The arena keeps rounds, not times: our k-th e-mail to a supplier takes the time of our k-th `send`
    decision to it in `known` (the run's own events) when there is one; everything else follows the
    previous event by a second. `with_messages`: False when the live file already holds the e-mails."""
    result = state.get("result") or {}
    sends = {}
    for event in known:
        if event.get("event") == "send" and event.get("supplier") and event.get("at"):
            sends.setdefault(event["supplier"], []).append(event["at"])
    clock = [min((e["at"] for e in known if e.get("at")), default=state.get("created_at") or 0) - 1]

    def tick(at=None):
        clock[0] = at if at and at > clock[0] else clock[0] + 1
        return clock[0]

    out, ours, theirs, sent, replied = [], {}, {}, {}, {}
    for step in result.get("trace") or []:
        actor, move = step.get("actor"), step.get("move") or {}
        if actor == "buyer":
            supplier, kind = move.get("supplier_id"), move.get("type")
            if kind == "message":
                k = sent[supplier] = sent.get(supplier, 0) + 1
                at = tick((sends.get(supplier) or [])[k - 1] if k <= len(sends.get(supplier) or []) else None)
                sheets = [sheet.get("terms") or {} for sheet in move.get("term_sheets") or []]
                if sheets and sheets[0].get("unit_price") is not None:
                    ours.setdefault(supplier, []).append(sheets[0]["unit_price"])
                if with_messages:
                    out.append(
                        {
                            "event": "message_out",
                            "at": at,
                            "supplier": supplier,
                            "subject": move.get("subject"),
                            "body": move.get("body"),
                            "sheets": sheets,
                            "refs": move.get("refs"),
                        }
                    )
            elif kind in ("accept", "walk_away"):
                at = tick()
                if with_messages:
                    action = {"type": kind, "term_sheet_id": move.get("offer_id")}
                    out.append({"event": "message_out", "at": at, "supplier": supplier, "action": action})
            elif kind == "timeout":
                out.append({"event": "timeout", "at": tick(), "supplier": supplier, "round": step.get("round")})
            continue
        at = tick()
        replied[(actor, step.get("round"))] = at
        terms, alternatives = move.get("terms") or {}, move.get("alternatives") or []
        if terms.get("unit_price") is not None:
            theirs.setdefault(actor, []).append(terms["unit_price"])
        if with_messages:
            out.append(
                {
                    "event": "message_in",
                    "at": at,
                    "supplier": actor,
                    "sender": actor,
                    "status": move.get("status"),
                    "body": move.get("text"),
                    "sheets": [terms] + [a.get("terms") for a in alternatives],
                    "offer_ids": [move.get("offer_id")] + [a.get("offer_id") for a in alternatives],
                    "claim_results": move.get("claim_results"),
                }
            )
            out.append(
                {
                    "event": "thread_state",
                    "at": at,
                    "supplier": actor,
                    "phase": move.get("status") or "",
                    "their_prices": list(theirs.get(actor, [])),
                    "our_prices": list(ours.get(actor, [])),
                }
            )
        reasons = step.get("reasons")
        if reasons:
            text = " ".join(reasons) if isinstance(reasons, list) else str(reasons)
            out.append(
                {
                    "event": "supplier_note",
                    "at": at,
                    "supplier": actor,
                    "round": step.get("round"),
                    "sender": "bot reasoning",
                    "text": text,
                }
            )
    for note in state.get("backoffice") or []:
        supplier = note.get("supplier_id")
        out.append(
            {
                "event": "supplier_note",
                "at": replied.get((supplier, note.get("round")), clock[0]),
                "supplier": supplier,
                "round": note.get("round"),
                "sender": note.get("from"),
                "recipient": note.get("to"),
                "text": note.get("text"),
            }
        )
    costs = {k: result.get(k) for k in ("c_open", "c_deal", "c_best", "cost_breakdown")}
    ends = [e for e in known if e.get("event") == "end"]
    for end in ends:  # the run's own result line gets the arena's costs it lacks
        end.update({k: v for k, v in costs.items() if end.get(k) is None})
    if not ends and "score" in result:
        out.append(
            {
                "event": "end",
                "at": tick(),
                "supplier": result.get("supplier_id"),
                "score": result.get("score"),
                "deal": result.get("deal"),
                **costs,
            }
        )
    return out


def run_events(session):
    """Everything recorded about one run, as the event stream the case view replays: the live-file
    segment when there is one, our decisions from the run journal when the live file lacks them, and
    from the pulled arena session the e-mails (when the live file lacks them), the back office, the
    bot's reasons and the result."""
    run = recorded_runs(CONFIG["roots"] or checkouts()).get(session)
    if run is None:
        return {"found": False, "events": []}
    events = []
    if run["live"]:
        path, index = run["live"]
        events = [dict(e) for e in live_segments(read_events(Path(path)))[index]]  # copies: the cache stays as read
    if run["journal"] and not any(e.get("event") == "send" for e in events):
        journal = (read_json(Path(run["journal"])) or {}).get("journal") or []
        events += [dict(e) for e in journal if isinstance(e, dict) and e.get("event")]
    state = read_json(Path(run["arena"])) if run["arena"] else None
    if isinstance(state, dict):
        chat = any(e.get("event") in CHAT_EVENTS for e in events)
        events += arena_events(state, events, with_messages=not chat)
        suppliers = list(state.get("threads") or {})
    else:
        suppliers = sorted({e["supplier"] for e in events if e.get("supplier")})
    first = min((e["at"] for e in events if e.get("at")), default=run["at"] or 0)
    if not any(e.get("event") == "case_start" for e in events):
        head = {k: run[k] for k in ("title", "type", "goal")}
        events.insert(0, {"event": "case_start", "at": first, "case_id": run["case"], **head})
    if not any(e.get("event") == "session" for e in events):
        events.insert(1, {"event": "session", "at": first, "session_id": session, "suppliers": suppliers})
    events.sort(key=lambda e: e.get("at") or first)
    return {"found": True, "run": run_summary(run), "events": events}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/cases":
            payload = {
                "cases": all_cases(),
                "running": len(running_sessions()),
                "spend": spend(),
                "arena": CONFIG["arena"],
                "now": time.time(),
            }
        elif url.path == "/api/events":
            case = (query.get("case") or [""])[0]
            start = int((query.get("from") or ["0"])[0] or 0)
            payload = case_events(case, start)
        elif url.path == "/api/scores":
            payload = scores()
        elif url.path == "/api/history":
            payload = history()
        elif url.path == "/api/run":
            payload = run_events((query.get("session") or [""])[0])
        elif url.path in ("/", "/index.html"):
            return self.reply(PAGE_FILE.read_bytes(), "text/html; charset=utf-8")
        else:
            return self.send_error(404)
        return self.reply(json.dumps(payload, default=str).encode(), "application/json")

    def reply(self, body, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


PAGE_FILE = Path(__file__).with_suffix(".html")


def serve(port=8790, live=None, spend_file=None, arena=None, background=False, offline=False, reference=None):
    """Start the watch UI on 127.0.0.1:`port`. `background`: run it on a daemon thread next to the
    agent (`hackit2 run --gui`) and return the server; otherwise serve until interrupted.
    `offline`: never contact the arena (the team's best then comes from pulled sessions only).
    `reference`: the reference run's scores file (default $HACKIT_REFERENCE_FILE)."""
    CONFIG.update(
        live=Path(live) if live else CONFIG["live"],
        spend=spend_file or os.environ.get("HACKIT_SPEND_FILE", "logs/spend.json"),
        arena=(arena or os.environ.get("ARENA_URL") or CONFIG["arena"]).rstrip("/"),
        offline=offline,
        reference=reference or os.environ.get("HACKIT_REFERENCE_FILE") or None,
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if not offline:
        threading.Thread(target=team_loop, name="team-best", daemon=True).start()
    print(
        f"hackit2 Live on http://127.0.0.1:{port}  (reading {CONFIG['live']}"
        + ("; offline" if offline else "; team best from the arena every minute")
        + ")",
        flush=True,
    )
    if background:
        threading.Thread(target=server.serve_forever, name="liveview", daemon=True).start()
        return server
    server.serve_forever()
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hackit2 gui", description="hackit2 live watch UI")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--live", default=None, help="folder with the live case files (default logs/live)")
    parser.add_argument("--spend", default=None, help="model spend file (default $HACKIT_SPEND_FILE)")
    parser.add_argument("--arena", default=None, help="arena URL for replay links (default $ARENA_URL)")
    parser.add_argument("--offline", action="store_true", help="never contact the arena (no live team best)")
    parser.add_argument("--reference", default=None, help="reference run scores: manifest .json or digest .md")
    args = parser.parse_args(argv)
    serve(args.port, args.live, args.spend, args.arena, offline=args.offline, reference=args.reference)


if __name__ == "__main__":
    main()
