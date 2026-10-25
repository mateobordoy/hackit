"""Hollerau buyer Live: a local watch UI for running and finished cases (`buyer gui`).

Reads the live files the agent writes (`logs/live/case_<id>.jsonl`, see `telemetry.py`) and the run
logs (`logs/runs/*.json`, see `records.py`) and serves one page (`liveview.html`) that shows, per case:
the system map with the data that moved between the modules, the e-mail thread with term sheets and
claim checks, the belief about each supplier's floor and the planner's candidate table behind every
price, the preparation, the model calls and the decision feed. Scores compares the runs of each case
with our best, the team's best and the ProcureCabana reference; History opens any recorded run.

The only arena call is `GET /v1/me` once a minute for the team's best (never the endpoints the Live
Final forbids); `--offline` makes none. Standard library only. Contract: docs/liveview-contract.md.

    buyer gui                        # http://127.0.0.1:8790
    buyer gui --offline
    buyer practice --gui             # the agent and the UI in one process
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, records

CONFIG = {"live": None, "runs": None, "roots": None, "offline": False}
TEAM_REFRESH_S = 60
PAGE_FILE = Path(__file__).with_suffix(".html")
_cache = {}
_team = {"best": {}, "practice": {}, "at": None, "error": None}


def live_dir():
    return Path(CONFIG["live"] or config.LOG_DIR / "live")


def runs_dir():
    return Path(CONFIG["runs"] or records.RUNS)


def short_case(case_id):
    return str(case_id or "").split(":")[-1]


def checkouts():
    """Sibling checkouts (other worktrees of the team) whose logs this UI also shows."""
    if CONFIG["roots"] is not None:
        return [Path(r) for r in CONFIG["roots"]]
    here = config.ROOT.resolve()
    out = [here]
    try:
        out += [p for p in here.parent.iterdir() if p != here and (p / "logs").is_dir() and (p / "buyer").is_dir()]
    except OSError:
        pass
    return out


def source_name(root):
    return Path(root).resolve().name


# -- live files -------------------------------------------------------------------------------------
def read_events(path):
    """All JSON lines of a file, cached by size and modification time."""
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (stat.st_size, stat.st_mtime_ns)
    cached = _cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    events = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    _cache[path] = (key, events)
    return events


def segments(events):
    """The runs in one live file: each starts at a `case_start`."""
    starts = [i for i, e in enumerate(events) if e.get("event") == "case_start"] or [0]
    return [events[a:b] for a, b in zip(starts, [*starts[1:], len(events)], strict=True)]


def live_files():
    """{key: (path, source)}: this checkout's live files ("P05") and its siblings' ("P05@hackit-x")."""
    files, own = {}, live_dir()
    here = source_name(config.ROOT)
    if own.is_dir():
        for p in own.glob("case_*.jsonl"):
            files[p.stem.removeprefix("case_")] = (p, here)
    for root in checkouts():
        folder = Path(root) / "logs" / "live"
        if not folder.is_dir() or (own.is_dir() and folder.resolve() == own.resolve()):
            continue
        for p in folder.glob("case_*.jsonl"):
            files[f"{p.stem.removeprefix('case_')}@{source_name(root)}"] = (p, source_name(root))
    return files


def summarize(run, key=None, source=None):
    head = next((e for e in run if e.get("event") == "case_start"), {})
    end = next((e for e in reversed(run) if e.get("event") == "end"), None)
    result = (end or {}).get("result") or {}
    last = max((e.get("at") or 0 for e in run), default=0)
    stale = time.time() - last > 15 * 60
    return {
        "case": key or short_case(head.get("case_id")),
        "source": source,
        "case_id": head.get("case_id"),
        "title": head.get("title"),
        "type": head.get("type"),
        "goal": head.get("goal"),
        "set": head.get("set"),
        "session": head.get("session"),
        "status": "done" if end or stale else "live",
        "started": head.get("at"),
        "last": last,
        "suppliers": head.get("suppliers") or sorted({e["supplier"] for e in run if e.get("supplier")}),
        "c_alt": head.get("c_alt"),
        **{k: result.get(k) for k in ("deal", "supplier_id", "score", "c_deal", "end_reason")},
    }


def all_cases():
    out = []
    for key, (path, source) in live_files().items():
        runs = segments(read_events(path))
        if runs and runs[-1]:
            out.append(summarize(runs[-1], key, source))
    return sorted(out, key=lambda c: (c["status"] != "live", -(c["last"] or 0)))


def case_events(key, start=0):
    path = (live_files().get(key) or (live_dir() / f"case_{key}.jsonl", None))[0]
    runs = segments(read_events(path)) if path.is_file() else []
    run = runs[-1] if runs else []
    return {
        "events": with_transfers(run)[start:],
        "next": len(run),
        "run_start": run[0].get("at") if run else None,
    }


# -- the system map: which modules each event moved data between --------------------------------------
NOTE_FLOWS = {
    "setup": [("supplier", "policy", "Supplier profile"), ("economics", "policy", "Planned terms, q0, anchor")],
    "memory": [("memory", "belief", "Floor prior shift from earlier cases")],
    "clause_trade": [("economics", "policy", "Index clause trade")],
    "probe_sheet": [("economics", "policy", "Faster-payment probe package")],
    "need_sheet": [("economics", "policy", "Package for its need")],
    "reply": [("runner", "policy", "Its reply"), ("policy", "belief", "Belief update")],
    "rate_learned": [("supplier", "economics", "Cost of money learned")],
    "switch_terms": [("economics", "policy", "Switched terms")],
    "planner": [("belief", "planner", "Hypotheses still alive"), ("planner", "policy", "Next price")],
    "planner_accept": [("belief", "planner", "Hypotheses still alive"), ("planner", "policy", "Accept now")],
    "send": [("policy", "writer", "Price, sheets, claims"), ("writer", "runner", "E-mail")],
    "mirror": [("policy", "writer", "Mirror its cheaper sheet")],
    "last_sheet": [("economics", "policy", "Last sheet at our ceiling")],
    "accept": [("policy", "runner", "Accept")],
    "walk_away": [("policy", "runner", "Walk away")],
    "dominated": [("economics", "policy", "Bidder dominated")],
    "at_ceiling": [("economics", "policy", "At our ceiling")],
}


def with_transfers(events):
    """Copies of the events, each with the data it moved between two modules of the map."""
    out = []
    for i, original in enumerate(events):
        e = dict(original)
        kind, rows = e.get("event"), []

        def row(a, b, label, request, response=None, e=e, i=i, rows=rows):
            return {
                "id": f"{e.get('event')}:{i}:{len(rows)}",
                "from": a,
                "to": b,
                "label": label,
                "at": e.get("at"),
                "supplier": e.get("supplier"),
                "request": request,
                "response": response,
            }

        payload = {k: v for k, v in e.items() if k not in ("event", "at")}
        if kind == "message_in":
            rows.append(row("arena", "runner", "Seller e-mail", payload))
        elif kind == "message_out":
            rows.append(row("runner", "arena", "Buyer action" if e.get("action") else "Buyer e-mail", payload))
        elif kind == "timeout":
            rows.append(row("arena", "runner", "Too late", payload))
        elif kind == "llm":
            role = e.get("role") or "reader"
            rows.append(row("policy", role, f"{role} agent", {"prompt": e.get("prompt")}, e.get("answer")))
        elif kind == "note":
            what = e.get("what")
            for a, b, label in NOTE_FLOWS.get(what, []):
                rows.append(row(a, b, label, payload))
            if what == "setup" and e.get("claims"):
                rows.append(row("evidence", "policy", "Claims ranked by effect", {"claims": e.get("claims")}))
            if what == "setup" and e.get("prior"):
                rows.append(row("supplier", "belief", "Floor prior", {"prior": e.get("prior")}))
        elif kind == "end":
            rows.append(row("runner", "records", "Run log", payload))
            rows.append(row("runner", "memory", "Where the suppliers stopped", payload))
        e["transfers"] = rows
        out.append(e)
    return out


# -- scores and history ---------------------------------------------------------------------------------
def finished_runs():
    """Every run log of every checkout, oldest first."""
    out = []
    for root in checkouts():
        folder = runs_dir() if Path(root).resolve() == config.ROOT.resolve() else Path(root) / "logs" / "runs"
        for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            notes = d.get("notes") or []
            result = d.get("result") or {}
            out.append(
                {
                    "session": d.get("session"),
                    "case_id": d.get("case"),
                    "case": short_case(d.get("case")),
                    "set": d.get("set"),
                    "type": d.get("type"),
                    "goal": d.get("goal"),
                    "score": result.get("score"),
                    "deal": result.get("deal"),
                    "c_deal": result.get("c_deal"),
                    "end_reason": result.get("end_reason"),
                    "started": p.stat().st_mtime - max((n.get("t") or 0 for n in notes), default=0),
                    "source": source_name(root),
                    "path": str(p),
                }
            )
    return sorted(out, key=lambda r: r["started"] or 0)


def scores():
    runs = [r for r in finished_runs() if r["score"] is not None and r["set"] in ("practice", None)]
    by_case = {}
    for r in runs:
        by_case.setdefault(r["case"], []).append(r["score"])
    rows = []
    for case in sorted(set(by_case) | {short_case(c) for c in records.REFERENCE_BEST}):
        s = by_case.get(case, [])
        cid = f"case:{case}"
        rows.append(
            {
                "case": case,
                "latest": s[-1] if s else None,
                "previous": s[-2] if len(s) > 1 else None,
                "change": s[-1] - s[-2] if len(s) > 1 else None,
                "best": max(s) if s else None,
                "mean": sum(s) / len(s) if s else None,
                "n": len(s),
                "team_best": _team["best"].get(case),
                "reference_best": records.REFERENCE_BEST.get(cid),
                "reference_batch": records.REFERENCE_BATCH.get(cid),
                "runs": s[-12:],
            }
        )

    def total(field):
        vals = [r[field] for r in rows if r[field] is not None]
        return sum(vals) / len(vals) if vals else None

    return {
        "rows": rows,
        "totals": {f: total(f) for f in ("latest", "mean", "best", "team_best", "reference_batch", "reference_best")},
        "team": {"at": _team["at"], "error": _team["error"], "offline": CONFIG["offline"]},
    }


def history():
    live = {}
    for key, (path, source) in live_files().items():
        for run in segments(read_events(path)):
            s = summarize(run, key, source)
            if s["session"]:
                live[s["session"]] = s
    out = []
    for r in finished_runs():
        s = live.pop(r["session"], None)
        out.append({**r, "title": (s or {}).get("title"), "has_live": bool(s), "path": None})
    for s in live.values():  # running, or never finished
        out.append({**s, "has_live": True})
    return {"runs": sorted(out, key=lambda r: -(r.get("started") or 0))}


def run_events(session):
    """One recorded run as the event stream the case view replays: its live-file segment, else the
    run log's decision notes (no e-mails)."""
    for _, (path, _source) in live_files().items():
        for run in segments(read_events(path)):
            head = next((e for e in run if e.get("event") == "case_start"), {})
            if head.get("session") == session:
                return {"found": True, "events": with_transfers(run), "next": len(run), "run_start": head.get("at")}
    for r in finished_runs():
        if r["session"] != session:
            continue
        d = json.loads(Path(r["path"]).read_text(encoding="utf-8"))
        t0 = r["started"] or 0
        events = [
            {
                "event": "case_start",
                "at": t0,
                "case_id": d.get("case"),
                "type": d.get("type"),
                "goal": d.get("goal"),
                "set": d.get("set"),
                "session": session,
                "suppliers": sorted({n["supplier"] for n in d.get("notes") or [] if n.get("supplier")}),
            }
        ]
        events += [{"event": "note", "at": t0 + (n.get("t") or 0), **n} for n in d.get("notes") or []]
        events.append({"event": "end", "at": events[-1]["at"], "result": d.get("result")})
        return {"found": True, "events": with_transfers(events), "next": len(events), "run_start": t0}
    return {"found": False, "events": [], "next": 0, "run_start": None}


# -- the team's best ------------------------------------------------------------------------------------
def read_team_best():
    try:
        from arena_kit.client import ArenaClient

        me = ArenaClient().me()
        best = {short_case(k): v for k, v in (me.get("best") or {}).items() if v is not None}
        _team.update(best=best, practice=me.get("practice") or {}, at=time.time(), error=None)
    except Exception as exc:  # noqa: BLE001 - no network or key: the view shows our own runs only
        _team["error"] = str(exc)[:160]


def team_loop():
    while not CONFIG["offline"]:
        read_team_best()
        time.sleep(TEAM_REFRESH_S)


# -- the server -----------------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)

        def arg(name, default=""):
            return (q.get(name) or [default])[0]

        try:
            if url.path == "/api/cases":
                payload = all_cases()
            elif url.path == "/api/events":
                payload = case_events(arg("case"), int(arg("start", arg("from", "0")) or 0))
            elif url.path == "/api/scores":
                payload = scores()
            elif url.path == "/api/history":
                payload = history()
            elif url.path == "/api/run":
                payload = run_events(arg("session"))
            elif url.path in ("/", "/index.html"):
                return self.reply(PAGE_FILE.read_bytes(), "text/html; charset=utf-8")
            else:
                return self.send_error(404)
        except Exception as exc:  # noqa: BLE001 - one odd file must not take the page down
            return self.reply(json.dumps({"error": str(exc)}).encode(), "application/json", 500)
        return self.reply(json.dumps(payload, default=str).encode(), "application/json")

    def reply(self, body, kind, code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(port=8790, live=None, runs=None, roots=None, offline=False, background=False):
    """Start the UI on 127.0.0.1:`port`; `background` runs it on a daemon thread next to the agent."""
    CONFIG.update(live=live, runs=runs, roots=roots, offline=offline)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if not offline:
        threading.Thread(target=team_loop, name="team-best", daemon=True).start()
    print(f"Hollerau buyer Live on http://127.0.0.1:{port}  (reading {live_dir()})", flush=True)
    if background:
        threading.Thread(target=server.serve_forever, name="liveview", daemon=True).start()
        return server
    server.serve_forever()
    return server


def main(argv=None):
    ap = argparse.ArgumentParser(prog="buyer gui", description="Hollerau buyer live watch UI")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--live", default=None, help="folder with the live case files (default logs/live)")
    ap.add_argument("--offline", action="store_true", help="never contact the arena")
    args = ap.parse_args(argv)
    serve(args.port, args.live, offline=args.offline)


if __name__ == "__main__":
    main()
