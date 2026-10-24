"""hackit2 Live: a local watch UI for running (and finished) cases.

Reads the live files the agent writes (`logs/live/case_<id>.jsonl`, see `telemetry.py`) and
serves a single page that shows, per case: the module map with the code paths running right now and
the data flowing between them, the preparation team's thinking, every model call with prompt and
answer, the e-mail thread with term sheets and claim checks, the price paths and the decision trace.
Standard library only; it never contacts the arena.

    uv run hackit2 gui                          # http://127.0.0.1:8790, watch any run
    uv run hackit2 run --set practice --gui     # the agent and the UI in one process
    uv run hackit2 run --set final --no-gui     # headless: no UI and no live files at all
"""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CONFIG = {"live": Path("logs/live"), "spend": None, "arena": "https://arena.tacto.ai"}
_cache = {}


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


def serve(port=8790, live=None, spend_file=None, arena=None, background=False):
    """Start the watch UI on 127.0.0.1:`port`. `background`: run it on a daemon thread next to the
    agent (`hackit2 run --gui`) and return the server; otherwise serve until interrupted."""
    CONFIG.update(
        live=Path(live) if live else CONFIG["live"],
        spend=spend_file or os.environ.get("HACKIT_SPEND_FILE", "logs/spend.json"),
        arena=(arena or os.environ.get("ARENA_URL") or CONFIG["arena"]).rstrip("/"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"hackit2 Live on http://127.0.0.1:{port}  (reading {CONFIG['live']})", flush=True)
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
    args = parser.parse_args(argv)
    serve(args.port, args.live, args.spend, args.arena)


if __name__ == "__main__":
    main()
