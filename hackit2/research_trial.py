"""Measure preparation on real package data without opening an arena negotiation.

Example: uv run python -m hackit2.research_trial --case P02 --mode alpha --data ../package
Model calls use the normal shared spend limit. No ArenaClient, practice session or learning write.
"""

import argparse
import json
import os
import time
from pathlib import Path

from arena_kit.cli import load_env

from . import model, telemetry
from .cli import open_package
from .prep import briefing


def main(argv=None):
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="case ID in the local package, e.g. P02")
    parser.add_argument("--case-file", type=Path, help="saved case JSON or session JSON containing its case")
    parser.add_argument("--data", default=os.environ.get("ARENA_DATA", "../package"))
    parser.add_argument("--mode", choices=("alpha", "baseline"), default="alpha")
    parser.add_argument("--budget", type=float, default=40)
    parser.add_argument("--output", type=Path, default=Path("logs/research"))
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be positive")
    pkg = open_package(args.data)
    case_id = args.case if args.case.startswith("case:") else f"case:{args.case}"
    case = pkg.cases.get(case_id)
    if args.case_file:
        saved = json.loads(args.case_file.read_text())
        case = saved.get("case") if isinstance(saved.get("case"), dict) else saved
        if case.get("case_id") != case_id:
            raise SystemExit("--case-file does not match --case")
    if case is None:
        raise SystemExit(f"case not found in local package: {case_id}")
    complete = model.connect()
    if complete is None:
        raise SystemExit("no configured model transport; no trial was run")
    os.environ["HACKIT_RESEARCH_MODE"] = args.mode
    # A preparation benchmark must not silently time a cache hit.
    os.environ.pop(briefing.BRIEF_CACHE_ENV, None)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = args.output / f"{args.mode}-{case_id.removeprefix('case:')}-{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    token = telemetry.bind(folder / "events.jsonl")
    started = time.monotonic()
    before = model.counters()
    try:
        telemetry.emit("case_start", case_id=case_id, title=case.get("title"), research_only=True, mode=args.mode)
        briefs = briefing.prepare_briefs(case, pkg, complete, budget_s=args.budget)
    finally:
        telemetry.unbind(token)
    summary = {
        "case": case_id,
        "mode": args.mode,
        "seconds": round(time.monotonic() - started, 3),
        "budget_s": args.budget,
        "suppliers_prepared": len(briefs),
        "suppliers_expected": len(case.get("suppliers") or []),
        "models": model.counters_since(before),
        "research": {
            sid: {
                "reports": len(brief.get("research", {}).get("reports", {})),
                "sourced_findings": sum(
                    len(r["evidence"]) for r in brief.get("research", {}).get("reports", {}).values()
                ),
                "tool_calls": len(brief.get("research", {}).get("tool_calls", [])),
            }
            for sid, brief in briefs.items()
        },
        "arena_sessions_opened": 0,
        "status": "complete" if len(briefs) == len(case.get("suppliers") or []) else "partial" if briefs else "failed",
    }
    (folder / "briefs.json").write_text(json.dumps(briefs, indent=2, ensure_ascii=False))
    (folder / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({**summary, "output": str(folder)}, indent=2))
    if summary["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
