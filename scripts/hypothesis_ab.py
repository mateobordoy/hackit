"""A/B test of hypothesis learning on the local simulator, with the same frozen research in both arms.

1. Freeze: the nine-agent research runs once per case with the real model (shared spend cap) and is
   frozen under --cache (HACKIT_BRIEF_CACHE). A later run finds it there: no model call.
2. Arms: every case is played on a fresh simulator per repeat, hypotheses off, then on. Both arms read
   the same frozen briefs and make no other model call (reply reader and writer fall back to their
   templates), so the hypothesis logic is the only difference between the arms. The simulator's
   bots ignore e-mail text but react to term sheets, which is exactly what a played trade changes.
No practice session, no arena call, no lesson write.

    uv run python scripts/hypothesis_ab.py --cases S01 S02 S03 --repeats 2
"""

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from arena_kit import ArenaClient
from arena_kit.cli import load_env
from arena_kit.sim import Sim, create_app, load_cases

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hackit2 import model, session, settings  # noqa: E402
from hackit2.cli import open_package  # noqa: E402
from hackit2.prep import briefing  # noqa: E402


def frozen_only(system, user):
    """The arms' model: every call fails, so only frozen research is used and nothing else differs."""
    raise RuntimeError("A/B arms use frozen research only")


def serve(sim):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(sim), port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.01)
    return server, f"http://127.0.0.1:{port}"


def play(case_id, cases, pkg, arm, out):
    """One case on a fresh simulator with a frozen clock (no timeouts): result plus what the
    hypotheses did, from the run journal."""
    settings.HYPOTHESES = arm == "on"
    server, url = serve(Sim(load_cases(), clock=lambda: 1_000_000.0))
    try:
        key = ArenaClient(url).register(f"ab-{secrets.token_hex(3)}", "sim")["api_key"]
        table = session.PrefetchedTable(ArenaClient(url, key, rate=1000), cases[case_id])
        session.RUN_LOGS = out / f"runs-{arm}"
        result = session.run_case(table, case_id, pkg, complete=frozen_only, team=True, learn=False)
        journal = json.loads(next((session.RUN_LOGS).glob(f"*{table.session_id}.json")).read_text())["journal"]
    finally:
        server.should_exit = True
    played = [e for e in journal if e["event"] == "hypothesis_played"]
    updates = [e for e in journal if e["event"] == "hypothesis_update"]
    return {
        "case": case_id,
        "arm": arm,
        "score": result.get("score"),
        "deal": result.get("deal"),
        "c_deal": result.get("c_deal"),
        "rounds": sum(1 for e in journal if e["event"] == "send"),
        "played": [f"{e['id']}: {e['move']}" for e in played],
        "updates": [f"{e['id']} {e['outcome']} -> {e['p']}" for e in updates],
    }


def main(argv=None):
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", nargs="+", default=["S01", "S02", "S03"])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--data", default=os.environ.get("ARENA_DATA", "../package"))
    parser.add_argument("--cache", type=Path, default=Path("logs/hypothesis-ab/briefs"))
    parser.add_argument("--out", type=Path, default=Path("logs/hypothesis-ab"))
    parser.add_argument(
        "--no-freeze", action="store_true", help="never call the model: use only existing frozen briefs"
    )
    args = parser.parse_args(argv)
    os.environ["HACKIT_BRIEF_CACHE"] = str(args.cache.resolve())
    os.environ["HACKIT_RESEARCH_MODE"] = "alpha"
    args.out.mkdir(parents=True, exist_ok=True)
    pkg = open_package(args.data)
    cases = load_cases()
    ids = [c if c.startswith("case:") else f"case:{c}" for c in args.cases]
    if not args.no_freeze:
        complete = model.connect()
        for case_id in ids:
            briefs = briefing.prepare_briefs(cases[case_id], pkg, complete)
            count = sum(len(b.get("hypotheses") or []) for b in briefs.values())
            print(f"{case_id}: {len(briefs)} brief(s), {count} hypotheses frozen", flush=True)
    rows = []
    for repeat in range(args.repeats):
        for case_id in ids:
            for arm in ("off", "on"):
                row = play(case_id, cases, pkg, arm, args.out)
                row["repeat"] = repeat + 1
                rows.append(row)
                print(json.dumps(row), flush=True)
    (args.out / "results.json").write_text(json.dumps(rows, indent=1))
    print("\ncase | repeat | off score | on score | diff | on: played / updates")
    for case_id in ids:
        for repeat in range(1, args.repeats + 1):
            off = next(r for r in rows if r["case"] == case_id and r["repeat"] == repeat and r["arm"] == "off")
            on = next(r for r in rows if r["case"] == case_id and r["repeat"] == repeat and r["arm"] == "on")
            diff = (on["score"] or 0) - (off["score"] or 0)
            print(
                f"{case_id} | {repeat} | {off['score']:.3f} | {on['score']:.3f} | {diff:+.3f} | "
                f"{'; '.join(on['played']) or '-'} / {'; '.join(on['updates']) or '-'}"
            )


if __name__ == "__main__":
    main()
