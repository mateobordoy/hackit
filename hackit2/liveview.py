"""hackit2 Live: a local watch UI for running (and finished) cases.

Reads the live files the agent writes (`logs/live/case_<id>.jsonl`, see `telemetry.py`) and
serves a single page that shows, per case: the module map with the code paths running right now and
the data flowing between them, the preparation team's thinking, every model call with prompt and
answer, the e-mail thread with term sheets and claim checks, the price paths and the decision trace.

The Scores view compares the latest run of each case (from every worktree's `logs/runs/`) with the
earlier runs, the team's best and the reference run. The team's best comes from the sessions pulled
to `logs/sessions/` and, unless `offline`, from the arena's `GET /v1/me` once a minute: the only
call this UI ever makes to the arena (never the endpoints the Live Final forbids).
Standard library only.

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
_team = {"best": {}, "practice": {}, "at": None, "error": None}


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
        "model_calls": sum(1 for e in run if e.get("event") == "model_call"),
        "events": len(run),
    }


def all_cases():
    folder = CONFIG["live"]
    if not folder.is_dir():
        return []
    cases = [case_summary(p) for p in folder.glob("case_*.jsonl")]
    return sorted(cases, key=lambda c: (c["status"] != "live", -(c["last"] or 0)))


def case_events(case, start):
    path = CONFIG["live"] / f"case_{case}.jsonl"
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
        "team": {**_team, "offline": CONFIG["offline"], "pulled_cases": len(pulled)},
        "sources": sorted({run["source"] for run in runs}),
        "now": time.time(),
    }


def read_team_best():
    """The team's best score per practice case and its quota from `GET /v1/me`, into `_team`."""
    try:
        from arena_kit.client import ArenaClient

        me = ArenaClient().me()
        best = {short_case(k): v for k, v in (me.get("best") or {}).items() if v is not None}
        _team.update(best=best, practice=me.get("practice") or {}, at=time.time(), error=None)
    except Exception as exc:  # noqa: BLE001 - no network or key: the view shows the pulled sessions only
        _team["error"] = str(exc)[:160]


def team_loop():
    while not CONFIG["offline"]:
        read_team_best()
        time.sleep(TEAM_REFRESH_S)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/cases":
            payload = {"cases": all_cases(), "spend": spend(), "arena": CONFIG["arena"], "now": time.time()}
        elif url.path == "/api/events":
            case = (query.get("case") or [""])[0]
            start = int((query.get("from") or ["0"])[0] or 0)
            payload = case_events(case, start)
        elif url.path == "/api/scores":
            payload = scores()
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
