"""`uv run python -m hackit ...` - run the Hack It committee buyer.

  dossiers                       phase 0: write the dossier of every detailed supplier (rerun Sunday 08:00)
  play case:D01 [--no-model]     one case (sandbox D01/D02 are free, practice cases use the quota)
  run --set practice|sandbox     every hosted case of a set, in parallel
  run --set final                the Live Final: waits for each wave, prepares and plays its cases

Every case leaves its minutes in logs/hackit/.
"""

import argparse
import functools
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from arena_kit.cli import load_env, summary
from arena_kit.client import ArenaClient, ArenaError
from arena_kit.data import DataPackage
from arena_kit.play import RemoteTable, hosted, play

from . import dossier
from .buyer import CommitteeBuyer, load_dossiers

log = logging.getLogger("hackit")
DOSSIERS = "hackit-dossiers.json"


def package(path):
    for p in (path, os.environ.get("ARENA_DATA"), "data/package", "../data"):
        if p and Path(p).is_dir():
            pkg = DataPackage(p)
            pkg.lookup("")  # build the ID index once, before agents share it
            log.info("data package: %s (Sunday release: %s)", pkg.root, pkg.release)
            return pkg
    log.warning("no data package found: dossiers stay empty")
    return None


def set_of(case_id):
    return "sandbox" if case_id.startswith("case:D") else "practice"


def play_one(client, case, new_agent, deadline=None):
    """Prepare (no clock yet), then open and play one case."""
    agent = new_agent()
    try:
        agent.prepare(case)
    except Exception:  # noqa: BLE001 - preparing is a bonus, playing is the job
        log.exception("%s: prepare failed", case["case_id"])
    table = RemoteTable(client, case)
    result = play(table, case["case_id"], agent, deadline)
    if agent.minutes:
        agent.minutes.result(summary(result) | {"session_id": table.session_id})
    return table, result


def waves(client, new_agent, n_waves=5, parallel=10, poll=4.0):
    """The Live Final, as the kit's runner, with our preparation before each session opens."""
    started, closed, caps = {}, set(), {}

    def one(case):
        try:
            return play_one(client, case, new_agent, functools.partial(caps.get, case["wave"]))[1]
        except Exception as e:  # noqa: BLE001 - one broken case must not stop the others
            log.exception("%s failed", case["case_id"])
            return e

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while len(closed) < n_waves or not all(f.done() for f in started.values()):
            try:
                cases = client.cases("final")
            except ArenaError as e:
                if e.status != 403:
                    raise
                cases = []
            for case in cases:
                if not case.get("open"):
                    closed.add(case["wave"])
                    continue
                if case.get("seconds_left") is not None:
                    caps[case["wave"]] = time.time() + case["seconds_left"]
                if case["case_id"] not in started and case.get("seconds_left"):
                    log.warning("wave %s: starting %s", case["wave"], case["case_id"])
                    started[case["case_id"]] = pool.submit(one, case)
            time.sleep(poll)
    return {cid: f.result() for cid, f in started.items()}


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser(prog="hackit")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dossiers")
    d.add_argument("--data")
    p = sub.add_parser("play")
    p.add_argument("case_id")
    p.add_argument("--data")
    p.add_argument("--no-model", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--set", default="practice")
    r.add_argument("--data")
    r.add_argument("--parallel", type=int, default=8)
    r.add_argument("--waves", type=int, default=5)
    r.add_argument("--no-model", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
    )
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    pkg = package(args.data)
    if args.cmd == "dossiers":
        data = dossier.build_all(pkg, DOSSIERS)
        print(f"{len(data)} dossiers written to {DOSSIERS}")
        return
    cache = load_dossiers(DOSSIERS)
    new_agent = functools.partial(CommitteeBuyer, pkg, cache, not args.no_model)
    client = ArenaClient()
    if args.cmd == "play":
        case = next((c for c in client.cases(set_of(args.case_id)) if c["case_id"] == args.case_id), None)
        if case is None:
            raise SystemExit(f"{args.case_id}: no such case")
        table, result = play_one(client, case, new_agent)
        print(json.dumps(summary(result), indent=2))
        replay = client.session(table.session_id).get("replay_url")
        if replay:
            print(f"replay: {os.environ['ARENA_URL'].rstrip('/')}{replay}")
    elif args.set == "final":
        out = waves(client, new_agent, args.waves, min(args.parallel, 10))
        for cid, res in out.items():
            print(cid, res if isinstance(res, Exception) else summary(res))
    else:
        cases = hosted(client.cases(args.set))
        with ThreadPoolExecutor(max_workers=min(args.parallel, 10)) as pool:
            futures = {c["case_id"]: pool.submit(play_one, client, c, new_agent) for c in cases}
        scores = []
        for cid, f in futures.items():
            try:
                res = f.result()[1]
                scores.append(res.get("score") or 0)
                print(cid, summary(res))
            except Exception as e:  # noqa: BLE001
                print(cid, "failed:", e)
        if scores:
            print(f"mean score: {100 * sum(scores) / len(scores):.1f}")
    if client.practice_remaining is not None:
        print(f"practice sessions left today: {client.practice_remaining}")


if __name__ == "__main__":
    main()
