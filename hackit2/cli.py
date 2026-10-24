"""`hackit2` command line: play one case or a whole set with our agent, and pull finished sessions
(result, trace with the supplier's reasons, back office) into logs/sessions/ for analysis."""

import argparse
import functools
import json
import logging
import os
import statistics
from pathlib import Path

from arena_kit import ArenaClient, ArenaError, RemoteTable, play, play_all
from arena_kit.agents import NaiveAgent
from arena_kit.cli import load_env, summary
from arena_kit.data import DataPackage
from arena_kit.play import hosted, play_waves

from . import liveview, model, session, settings

SESSION_LOGS = Path("logs/sessions")
AGENTS = ("hackit", "naive")  # our engine, or the kit's baseline for comparison


def open_package(path):
    """The data package with its ID index built once, before parallel cases share it."""
    if not path:
        return None
    pkg = DataPackage(path)
    pkg.lookup("")
    return pkg


def pull_sessions(client, case_set):
    """Save every ended session of a set that has its trace to logs/sessions/<set>/, once."""
    folder = SESSION_LOGS / case_set
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for item in client.sessions(case_set):
        path = folder / f"{item['case_id'].split(':')[-1]}_{item['session_id']}.json"
        if item["status"] != "ended" or path.exists():
            continue
        state = client.session(item["session_id"])
        if not (state.get("result") or {}).get("trace"):  # final sessions get their trace after the final
            continue
        try:
            state["backoffice"] = client.backoffice(item["session_id"])
        except ArenaError:
            state["backoffice"] = None
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        saved += 1
    print(f"{saved} new sessions in {folder}")


def find_case_file(client, case_id):
    """The case file before its session opens (free, no quota) for the preparation team; None when the
    arena does not list it."""
    for case_set in ("sandbox", "practice"):
        for case in client.cases(case_set):
            if case["case_id"] == case_id:
                return case
    return None


def build_parser():
    parser = argparse.ArgumentParser(prog="hackit2")
    parser.add_argument("-v", "--verbose", action="store_true")
    shared = argparse.ArgumentParser(add_help=False)  # these flags also work after the command
    shared.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    shared.add_argument("--no-llm", action="store_true", default=argparse.SUPPRESS, help="template e-mails only")
    shared.add_argument(
        "--no-team",
        action="store_true",
        default=argparse.SUPPRESS,
        help="plain price engine: no preparation team, no reply reader, no debrief",
    )
    shared.add_argument(
        "--gui",
        action="store_true",
        default=argparse.SUPPRESS,
        help="also serve the live watch UI while the agent runs (http://127.0.0.1:8790)",
    )
    shared.add_argument(
        "--no-gui",
        action="store_true",
        default=argparse.SUPPRESS,
        help="headless: no watch UI and no live telemetry files (same as HACKIT_NO_GUI=1)",
    )
    shared.add_argument("--gui-port", type=int, default=argparse.SUPPRESS, help="port of the watch UI")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser = functools.partial(commands.add_parser, parents=[shared])
    data = {"default": os.environ.get("ARENA_DATA"), "help": "data package folder"}
    play_cmd = commands.add_parser("play", help="play one case")
    play_cmd.add_argument("case_id")
    play_cmd.add_argument("--agent", default="hackit", choices=AGENTS)
    play_cmd.add_argument("--data", **data)
    run_cmd = commands.add_parser("run", help="play every playable case of a set in parallel; final: wave by wave")
    run_cmd.add_argument("--set", default="practice", choices=["practice", "sandbox", "final"])
    run_cmd.add_argument("--agent", default="hackit", choices=AGENTS)
    run_cmd.add_argument("--data", **data)
    run_cmd.add_argument("--parallel", type=int, default=8, help="cases at once (at most 10)")
    run_cmd.add_argument("--waves", type=int, default=5, help="final: stop after this many waves have closed")
    run_cmd.add_argument("--cases", help="practice or sandbox: only these cases, e.g. P01,P08")
    pull_cmd = commands.add_parser("pull", help="save finished sessions with trace and back office to logs/sessions/")
    pull_cmd.add_argument("--set", default="practice", choices=["practice", "sandbox", "final"])
    gui_cmd = commands.add_parser("gui", help="serve the live watch UI only (watch runs started elsewhere)")
    gui_cmd.add_argument(
        "--offline", action="store_true", help="never contact the arena: the team's best from pulled sessions only"
    )
    return parser


def print_outcomes(outcomes):
    for case_id, result in outcomes.items():
        print(case_id, result if isinstance(result, Exception) else summary(result))


def main(argv=None):
    load_env()
    args = build_parser().parse_args(argv)
    gui_port = getattr(args, "gui_port", settings.GUI_PORT)
    if args.command == "gui":
        return liveview.serve(gui_port, offline=args.offline)
    if getattr(args, "no_gui", False):
        if getattr(args, "gui", False):
            raise SystemExit("--gui and --no-gui exclude each other")
        settings.LIVE_TELEMETRY = False
    elif getattr(args, "gui", False):
        if not settings.LIVE_TELEMETRY:
            raise SystemExit("--gui needs live telemetry: unset HACKIT_NO_GUI")
        # during the Live Final the UI reads no team scores from the arena
        liveview.serve(gui_port, background=True, offline=getattr(args, "set", None) == "final")
        print("the watch UI closes when this run ends; `uv run hackit2 gui` keeps watching afterwards")
    no_llm = getattr(args, "no_llm", False)
    team = not getattr(args, "no_team", False)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    client = ArenaClient()

    if args.command == "pull":
        return pull_sessions(client, args.set)
    naive = args.agent == "naive"
    pkg = None if naive else open_package(args.data)
    complete = None if naive or no_llm else model.connect()
    if args.command == "play":
        try:
            if naive:
                table = RemoteTable(client)
                result = play(table, args.case_id, NaiveAgent())
            else:
                table = session.PrefetchedTable(client, find_case_file(client, args.case_id))
                result = session.run_case(table, args.case_id, pkg, complete=complete, team=team)
        except ArenaError as exc:  # not hosted, the quota, a paused key: the arena says why
            raise SystemExit(str(exc)) from None
        print(json.dumps(summary(result), indent=2))
        state = client.session(table.session_id)
        print(f"replay: {os.environ['ARENA_URL'].rstrip('/')}{state.get('replay_url')}")
        pull_sessions(client, state["set"])
    elif args.set == "final":  # no human in the loop: waits for each wave and plays it within its cap
        parallel = min(args.parallel, 10)
        if naive:
            outcomes = play_waves(client, NaiveAgent, args.waves, parallel)
        else:
            outcomes = session.run_final(client, pkg, args.waves, parallel, complete=complete, team=team)
        print_outcomes(outcomes)
    else:
        cases = hosted(client.cases(args.set))
        if args.cases:
            wanted = args.cases.split(",")
            cases = [c for c in cases if c["case_id"].split(":")[-1] in wanted]
        parallel = min(args.parallel, 10)
        if naive:
            outcomes = play_all(lambda: RemoteTable(client), [c["case_id"] for c in cases], NaiveAgent, parallel)
        else:
            outcomes = session.run_batch(client, cases, pkg, parallel, complete=complete, team=team)
        print_outcomes(outcomes)
        scores = [r["score"] for r in outcomes.values() if isinstance(r, dict) and r.get("score") is not None]
        if scores:
            print(f"mean score: {100 * statistics.mean(scores):.1f} over {len(scores)} cases")
        pull_sessions(client, args.set)
    if client.practice_remaining is not None:
        print(f"practice sessions left today: {client.practice_remaining}")


if __name__ == "__main__":
    main()
