"""`buyer` command line.

buyer data                         download the data package (+ Sunday release once public)
buyer cases [--set practice]       list the case files
buyer me                           practice quota and best scores
buyer play case:P01 [--repeat 2]   play one case (practice quota!)
buyer practice --repeat 2          every hosted practice case N times (practice quota!)
buyer sandbox                      the free sandbox cases D01, D02
buyer final                        the Live Final: waits for each wave, plays it, never stops early
buyer report                       scores from logs/runs next to the ProcureCabana reference
"""

import argparse
import io
import json
import logging
import os
import statistics
import zipfile
from pathlib import Path

from . import config

SUMMARY = ("deal", "supplier_id", "c_open", "c_deal", "c_best", "score", "end_reason")


def summary(result):
    if isinstance(result, Exception):
        return f"ERROR {result}"
    return {k: result.get(k) for k in SUMMARY if k in result}


def setup_logging(verbose):
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(config.LOG_DIR / "buyer.log", encoding="utf-8"), logging.StreamHandler()]
    handlers[1].setLevel(logging.INFO if verbose else logging.WARNING)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def client():
    from arena_kit.client import ArenaClient

    return ArenaClient()


def print_batch(out):
    scores = []
    for cid, result in out:
        print(cid, json.dumps(summary(result), default=str))
        if isinstance(result, dict) and result.get("score") is not None:
            scores.append(result["score"])
    if scores:
        print(f"sessions: {len(scores)}  mean score: {100 * statistics.mean(scores):.1f}")


def main(argv=None):
    config.load_env()
    ap = argparse.ArgumentParser(prog="buyer")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data")
    d.add_argument("--out", default=str(config.ROOT / "data"))
    c = sub.add_parser("cases")
    c.add_argument("--set", default="practice")
    sub.add_parser("me")
    p = sub.add_parser("play")
    p.add_argument("case_id")
    p.add_argument("--set", default="practice")
    p.add_argument("--repeat", type=int, default=1)
    pr = sub.add_parser("practice")
    pr.add_argument("--repeat", type=int, default=2)
    pr.add_argument("--parallel", type=int, default=5)
    pr.add_argument("--cases", default="", help="comma list, e.g. P01,P08 (default: every hosted case)")
    sb = sub.add_parser("sandbox")
    sb.add_argument("--repeat", type=int, default=1)
    f = sub.add_parser("final")
    f.add_argument("--waves", type=int, default=5)
    f.add_argument("--parallel", type=int, default=10)
    r = sub.add_parser("report")
    r.add_argument("--set", default="practice")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)

    if args.cmd == "report":
        from .records import report

        print(report(set_=args.set))
        return
    if args.cmd == "data":
        cl = client()
        for name, info in cl.downloads().items():
            if info.get("url"):
                zipfile.ZipFile(io.BytesIO(cl.download(name))).extractall(args.out)
                print(f"{name}: {Path(args.out) / name}")
            else:
                print(f"{name}: public from {info.get('at')}")
        return
    from arena_kit.play import hosted

    from .runner import play_batch, play_waves

    cl = client()
    if args.cmd == "cases":
        cases = cl.cases(args.set)
        playable = {x["case_id"] for x in hosted(cases)}
        for case in cases:
            mark = "" if case["case_id"] in playable else "  (not hosted)"
            goal = case["buyer"].get("category_goal", "")
            print(case["case_id"], case.get("type", ""), goal, case.get("title", "") + mark)
    elif args.cmd == "me":
        print(json.dumps(cl.me(), indent=2))
    elif args.cmd == "play":
        cases = {x["case_id"]: x for x in cl.cases(args.set)}
        cid = args.case_id if args.case_id.startswith("case:") else f"case:{args.case_id}"
        if cid not in cases:
            raise SystemExit(f"{cid} is not in the {args.set} list")
        print_batch(play_batch(cl, [cases[cid]], args.repeat, 1))
    elif args.cmd == "practice":
        cases = hosted(cl.cases("practice"))
        if args.cases:
            want = {x if x.startswith("case:") else f"case:{x}" for x in args.cases.split(",")}
            cases = [x for x in cases if x["case_id"] in want]
        print(f"{len(cases)} cases x {args.repeat} = {len(cases) * args.repeat} practice sessions")
        print_batch(play_batch(cl, cases, args.repeat, args.parallel))
    elif args.cmd == "sandbox":
        print_batch(play_batch(cl, cl.cases("sandbox"), args.repeat, 2))
    elif args.cmd == "final":
        out = play_waves(cl, args.waves, min(args.parallel, 10))
        print_batch(list(out.items()))
    if cl.practice_remaining is not None:
        print(f"practice sessions left today: {cl.practice_remaining}")
    if os.environ.get("LLM_MODEL"):
        from .llm import STATS

        print(f"model: {STATS}")


if __name__ == "__main__":
    main()
