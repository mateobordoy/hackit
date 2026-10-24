"""Bounded, interleaved A/B trials; sandbox by default, practice requires explicit authorization.

uv run python scripts/adviser_experiment.py --mode templates --pairs 2
uv run python scripts/adviser_experiment.py --mode production --pairs 1
uv run python scripts/adviser_experiment.py --practice --mode production --pairs 1 --cases P11 P10 P05

Each arm runs in a separate process. No lessons are written. Full supplier replies, backoffice,
round-trip times, decision journals and a small metrics manifest stay in ignored logs/.
"""

import argparse
import fcntl
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


def worker(args, folder):
    from arena_kit import ArenaClient
    from arena_kit.cli import load_env

    load_env()  # Normal application credential loading; never print the environment or credentials.
    from hackit2 import adviser_priorities, model, session, settings
    from hackit2.cli import open_package

    settings.SELLER_ADVISER_INQUIRY = args.arm == "on"
    settings.LIVE_TELEMETRY = False
    folder.mkdir(parents=True, exist_ok=True)
    session.RUN_LOGS = folder / "runs"
    client = ArenaClient()
    case_id = f"case:{args.cases[0]}"
    arena_set = "practice" if args.practice else "sandbox"
    if args.practice:
        hub_state = json.loads((Path(__file__).resolve().parents[2] / "agents-hub/state.json").read_text())
        if not any(
            c["agent"] == "codex" and c["path"] == "@arena-practice" and c["until"] > time.time()
            for c in hub_state["claims"]
        ):
            raise SystemExit("Practice requires Codex's active @arena-practice claim and Burak's explicit OK.")
    cases = {c["case_id"]: c for c in client.cases(arena_set) if c.get("hosted", True)}
    if case_id not in cases:
        raise SystemExit(f"Not a hosted {arena_set} case: {case_id}")
    replies, sends = [], []

    class MeasuredTable(session.PrefetchedTable):
        def send(self, supplier_id, message):
            started = time.monotonic()
            reply = super().send(supplier_id, message)
            sends.append({"supplier": supplier_id, "message": message, "seconds": round(time.monotonic() - started, 3)})
            replies.append({"supplier": supplier_id, "reply": reply})
            return reply

    table = MeasuredTable(client, cases[case_id])
    complete = model.connect() if args.mode == "production" else None
    package = open_package(os.environ.get("ARENA_DATA"))
    spent_before = model.spent_eur()
    started = time.monotonic()
    result = session.run_case(table, case_id, package, complete=complete, team=True, learn=False)
    elapsed = time.monotonic() - started
    state = client.session(table.session_id)
    state["backoffice"] = client.backoffice(table.session_id)
    (folder / "session.json").write_text(json.dumps(state, indent=2, ensure_ascii=False))
    (folder / "exchanges.json").write_text(json.dumps({"sends": sends, "replies": replies}, indent=2))
    run = json.loads(next(session.RUN_LOGS.glob("*.json")).read_text())
    journal = run["journal"]
    counters = {}
    for item in replies:
        reply = item["reply"]
        sheets = reply.get("term_sheets") or []
        if sheets and item["supplier"] not in counters:
            counters[item["supplier"]] = sheets[0]["terms"].get("unit_price")
    metrics = {
        "case": case_id,
        "set": arena_set,
        "mode": args.mode,
        "arm": args.arm,
        "session": table.session_id,
        "score": result.get("score"),
        "deal": result.get("deal"),
        "must_haves_ok": result.get("must_haves_ok"),
        "c_deal": result.get("c_deal"),
        "seconds": round(elapsed, 3),
        "mean_reply_seconds": round(statistics.mean(s["seconds"] for s in sends), 3) if sends else None,
        "max_reply_seconds": max((s["seconds"] for s in sends), default=None),
        "first_counters": counters,
        "messages": len(sends),
        "questions": sum(adviser_priorities.QUESTION in s["message"].get("body", "") for s in sends),
        "priority_events": [e for e in journal if e["event"] == "adviser_priorities"],
        "need_alternatives": [e for e in journal if e["event"] == "need_alternative"],
        "timeouts": sum(e["event"] == "undo_delivery" for e in journal),
        "model_spend_eur": round(model.spent_eur() - spent_before, 4),
    }
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(
        json.dumps(
            {
                k: metrics[k]
                for k in (
                    "case",
                    "set",
                    "mode",
                    "arm",
                    "session",
                    "score",
                    "seconds",
                    "messages",
                    "first_counters",
                    "timeouts",
                    "model_spend_eur",
                )
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("templates", "production"), default="templates")
    parser.add_argument("--pairs", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--practice", action="store_true", help="only with Burak's OK and the hub claim")
    parser.add_argument("--cases", nargs="+", default=["D02", "D01"])
    parser.add_argument("--arm", choices=("off", "on"), help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=Path("logs/adviser-experiment"))
    args = parser.parse_args()
    allowed = {"P11", "P10", "P05"} if args.practice else {"D01", "D02"}
    if not set(args.cases) <= allowed:
        parser.error(f"Only these cases are allowed for this experiment: {sorted(allowed)}")
    if args.practice and not args.arm and args.pairs != 1:
        parser.error("Practice is bounded to one pair per case; increase only after coordinating a new quota.")
    if args.arm:
        args.output.mkdir(parents=True, exist_ok=True)
        with (args.output / ".trial.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if (args.output / "metrics.json").exists():
                print(f"Reusing completed trial: {args.output}", flush=True)
                return
            return worker(args, args.output)
    root = args.output / f"{time.strftime('%Y%m%d-%H%M%S')}-{'practice-' if args.practice else ''}{args.mode}"
    rows = []
    for pair in range(args.pairs):
        for case in args.cases:
            # Reverse the second pair to reduce order effects as well as interleaving arms.
            for arm in ("off", "on") if pair % 2 == 0 else ("on", "off"):
                folder = root / f"{case}-pair{pair + 1}-{arm}"
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--mode",
                    args.mode,
                    "--cases",
                    case,
                    "--arm",
                    arm,
                    "--output",
                    str(folder),
                ]
                if args.practice:
                    command.append("--practice")
                print(f"Starting {case} pair {pair + 1} {arm} ({args.mode})", flush=True)
                subprocess.run(command, check=True)
                metrics = json.loads((folder / "metrics.json").read_text())
                rows.append(metrics)
                (root / "manifest.json").write_text(json.dumps(rows, indent=2))
    print(f"Saved {len(rows)} {'practice' if args.practice else 'sandbox'} trials in {root}", flush=True)


if __name__ == "__main__":
    main()
