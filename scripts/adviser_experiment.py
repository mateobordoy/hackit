"""T21 controlled practice cohort; requires Burak's OK and the @arena-practice claim.

uv run python scripts/adviser_experiment.py --practice --cases P03 P04 P05 P07 --pairs 2 \
    --parallel 2 --output logs/adviser-experiment-v4/cohort

Briefs are prepared and frozen BEFORE any session opens. First-turn reader/writer OUTCOMES are
cached after their guard/timeout, including fallback markers. Separate processes run each arm,
learning is off, and an attempt ledger prevents reruns from silently spending extra quota.
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
CASES_FILE = WORKTREE / "logs/adviser-experiment-v4/cases.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def source_fingerprint():
    paths = sorted((WORKTREE / "hackit2").rglob("*.py")) + [Path(__file__).resolve()]
    return digest({str(path.relative_to(WORKTREE)): path.read_text() for path in paths})


@contextlib.contextmanager
def json_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {}
        yield state
        path.write_text(json.dumps(state, indent=2))


def reserve_trial(root, trial):
    """Count every session-open attempt once, including uncertain network outcomes. Never auto-retry."""
    limit = min(16, json.loads((root / "cohort.json").read_text())["max_sessions"])
    with json_lock(root / "attempts.json") as state:
        attempts = state.setdefault("attempts", [])
        if any(a["trial"] == trial for a in attempts):
            raise RuntimeError(f"Session open already attempted for {trial}; inspect the saved session before resuming")
        if len(attempts) >= limit:
            raise RuntimeError(f"Authorized cohort cap reached ({limit})")
        attempts.append({"trial": trial, "at": time.time()})


def install_opening_cache(folder):
    """Freeze outcomes actually USED by the agent; a late raw result cannot replace a fallback."""
    from hackit2 import model, reply_reader

    counts = {"reader_hit": 0, "reader_miss": 0, "writer_hit": 0, "writer_miss": 0}
    version = digest([Path(model.__file__).read_text(), Path(reply_reader.__file__).read_text()])
    original_read, original_write = reply_reader.read_reply, model.write_paragraph

    def cached(role, inputs, call):
        path = folder / f"{digest([version, role, inputs])}.json"
        with json_lock(path) as record:
            if "outcome" in record:
                counts[f"{role}_hit"] += 1
                return record["outcome"]
            outcome = call()
            record["outcome"] = outcome  # None is a first-class, frozen template/unavailable marker.
            counts[f"{role}_miss"] += 1
            return outcome

    def read(
        complete,
        case,
        body,
        our_prices,
        their_prices,
        expected_arguments=(),
        timeout=None,
        history=None,
        supplier_id=None,
    ):
        def call():
            return original_read(
                complete,
                case,
                body,
                our_prices,
                their_prices,
                expected_arguments,
                timeout,
                history=history,
                supplier_id=supplier_id,
            )

        if our_prices:
            return call()
        inputs = [case, body, our_prices, their_prices, expected_arguments, history, supplier_id]
        return cached("reader", inputs, call)

    def write(complete, prompt, timeout=None, supplier_id=None):
        def call():
            return original_write(complete, prompt, timeout, supplier_id=supplier_id)

        if "\nQUESTION ALLOWED: yes" not in prompt:
            return call()
        result = cached("writer", [prompt, supplier_id], call)
        # Versioned keys already include the guard; revalidate hits as a second check.
        return None if result and model.UNSAFE_TEXT.search(result) else result

    reply_reader.read_reply, model.write_paragraph = read, write
    return counts


def worker(args, root, folder):
    from arena_kit import ArenaClient
    from arena_kit.cli import load_env

    load_env()  # Normal application credential loading; never print keys or the environment.
    from hackit2 import adviser_priorities, model, session, settings
    from hackit2.cli import open_package

    state = json.loads((WORKTREE.parent / "agents-hub/state.json").read_text())
    if not any(
        c["agent"] == "codex" and c["path"] == "@arena-practice" and c["until"] > time.time() for c in state["claims"]
    ):
        raise RuntimeError("An active Codex @arena-practice claim and Burak's explicit OK are required")
    cohort = json.loads((root / "cohort.json").read_text())
    if source_fingerprint() != cohort["source_hash"]:
        raise RuntimeError("Source changed after cohort freeze; refusing to open a session")
    cid = f"case:{args.case}"
    if args.case not in cohort["cases"]:
        raise RuntimeError("Case not authorized in this cohort")
    case = next(c for c in json.loads(CASES_FILE.read_text()) if c["case_id"] == cid)
    brief_path = root / "briefs" / f"{args.case}.json"
    brief_hash = digest(json.loads(brief_path.read_text()))
    if brief_hash != cohort["brief_hashes"][args.case]:
        raise RuntimeError("Frozen brief changed; refusing to open a session")
    os.environ["HACKIT_BRIEF_CACHE"] = str(root / "briefs")
    os.environ["HACKIT_SELLER_ADVISERS"] = "1" if args.arm == "on" else "0"
    settings.SELLER_ADVISER_INQUIRY = args.arm == "on"
    settings.LIVE_TELEMETRY = False
    session.RUN_LOGS = folder / "runs"
    cache_counts = install_opening_cache(root / "opening-outcomes")
    client, replies, sends = ArenaClient(), [], []

    class MeasuredTable(session.PrefetchedTable):
        def open(self, case_id):
            reserve_trial(root, folder.name)
            opened = super().open(case_id)
            (folder / "opened.json").write_text(json.dumps({"session": self.session_id}))
            return opened

        def send(self, supplier_id, message):
            started = time.monotonic()
            reply = super().send(supplier_id, message)
            sends.append({"supplier": supplier_id, "message": message, "seconds": time.monotonic() - started})
            replies.append({"supplier": supplier_id, "reply": reply})
            (folder / "progress.json").write_text(json.dumps({"session": self.session_id, "messages": len(sends)}))
            return reply

    table = MeasuredTable(client, case)
    complete, package = model.connect(), open_package(os.environ.get("ARENA_DATA"))
    spent_before, counters_before = model.spent_eur(), model.counters()
    started = time.monotonic()
    result = session.run_case(table, cid, package, complete=complete, team=True, learn=False)
    elapsed = time.monotonic() - started
    snapshot = client.session(table.session_id)
    snapshot["backoffice"] = client.backoffice(table.session_id)  # Completed sessions only; never agent input.
    (folder / "session.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    (folder / "exchanges.json").write_text(json.dumps({"sends": sends, "replies": replies}, indent=2))
    journal = json.loads(next(session.RUN_LOGS.glob("*.json")).read_text())["journal"]
    first, second, opening, matches, seen = {}, {}, {}, [], {}
    for sent, received in zip(sends, replies, strict=True):
        sid, reply = sent["supplier"], received["reply"]
        seen[sid] = seen.get(sid, 0) + 1
        sheets = reply.get("term_sheets") or []
        if sheets:
            if seen[sid] == 1:
                first[sid] = sheets[0]["terms"]
            if seen[sid] == 2:
                second[sid] = sheets[0]["terms"]
        if seen[sid] == 1:
            opening[sid] = {k: sent["message"].get(k) for k in ("body", "term_sheets", "refs")}
        offered = sent["message"].get("term_sheets") or []
        if not offered or not sheets:
            continue
        primary = offered[0]["terms"]
        for extra in offered[1:]:
            changed = {k: v for k, v in extra["terms"].items() if k != "unit_price" and primary.get(k) != v}
            if changed and all(sheets[0]["terms"].get(k) == v for k, v in changed.items()):
                matches.append(
                    {
                        "supplier": sid,
                        "round": seen[sid],
                        "fields": changed,
                        "reply_terms": sheets[0]["terms"],
                        "status": reply.get("status"),
                    }
                )
    metrics = {
        "case": cid,
        "arm": args.arm,
        "pair": args.pair,
        "session": table.session_id,
        "score": result.get("score"),
        "deal": result.get("deal"),
        "must_haves_ok": result.get("must_haves_ok"),
        "final_terms": result.get("terms"),
        "rounds": snapshot.get("rounds"),
        "seconds": round(elapsed, 3),
        "messages": len(sends),
        "first_counters": first,
        "second_counters": second,
        "opening_hashes": {k: digest(v) for k, v in opening.items()},
        "extra_sheet_matches": matches,
        "questions": sum(adviser_priorities.VALUE_QUESTION in s["message"].get("body", "") for s in sends),
        "probes": [e for e in journal if e["event"] == "value_probe"],
        "priority_events": [e for e in journal if e["event"] == "adviser_priorities"],
        "timeouts": sum(e["event"] == "undo_delivery" for e in journal),
        "mean_send_seconds": statistics.mean(s["seconds"] for s in sends) if sends else None,
        "brief_hash": brief_hash,
        "source_hash": cohort["source_hash"],
        "brief_unchanged": brief_hash == digest(json.loads(brief_path.read_text())),
        "opening_cache": cache_counts,
        "model_counters": model.counters_since(counters_before),
        "shared_spend_delta_eur": round(model.spent_eur() - spent_before, 4),
    }
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(
        json.dumps(
            {k: metrics[k] for k in ("case", "arm", "pair", "session", "score", "seconds", "questions", "timeouts")}
        ),
        flush=True,
    )


def seed_briefs(root, cases):
    from arena_kit.cli import load_env

    load_env()
    from hackit2 import model
    from hackit2.cli import open_package
    from hackit2.prep.briefing import prepare_briefs

    os.environ["HACKIT_BRIEF_CACHE"] = str(root / "briefs")
    package, complete = open_package(os.environ.get("ARENA_DATA")), model.connect()
    hashes = {}
    data = {c["case_id"].removeprefix("case:"): c for c in json.loads(CASES_FILE.read_text())}
    for cid in cases:
        print(f"Freezing preparation for {cid} (no session open)", flush=True)
        brief = prepare_briefs(data[cid], package, complete)
        path = root / "briefs" / f"{cid}.json"
        if not brief or not path.exists():
            raise RuntimeError(f"No frozen brief for {cid}; no practice sessions will start")
        hashes[cid] = digest(json.loads(path.read_text()))
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--practice", action="store_true")
    parser.add_argument("--cases", nargs="+", default=["P03", "P04", "P05", "P07"])
    parser.add_argument("--pairs", type=int, default=2)
    parser.add_argument("--parallel", type=int, choices=(1, 2), default=2)
    parser.add_argument("--output", type=Path, default=Path("logs/adviser-experiment-v4/cohort"))
    parser.add_argument("--arm", choices=("off", "on"), help=argparse.SUPPRESS)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    parser.add_argument("--pair", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.output.resolve()
    if not args.practice:
        parser.error("This is a practice experiment; --practice requires Burak's explicit OK and the hub claim")
    if args.arm:
        folder = root / f"{args.case}-pair{args.pair}-{args.arm}"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / ".trial.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if (folder / "metrics.json").exists():
                print(f"Reusing {folder.name}", flush=True)
                return
            return worker(args, root, folder)
    if args.pairs < 1 or len(set(args.cases)) != len(args.cases) or len(args.cases) * args.pairs * 2 > 16:
        parser.error("Unique cases and positive pairs required; the total may not exceed 16 sessions")
    if not set(args.cases) <= {"P01", "P02", "P03", "P04", "P05", "P07", "P08", "P10", "P11", "P18"}:
        parser.error("Only hosted practice cases are allowed")
    root.mkdir(parents=True, exist_ok=True)
    cohort = {"cases": args.cases, "pairs": args.pairs, "max_sessions": len(args.cases) * args.pairs * 2}
    cohort["source_hash"] = source_fingerprint()
    if (root / "cohort.json").exists():
        old = json.loads((root / "cohort.json").read_text())
        if any(old[k] != v for k, v in cohort.items()):
            raise RuntimeError("Cannot change a cohort after it has been initialized")
    cohort["brief_hashes"] = seed_briefs(root, args.cases)
    (root / "cohort.json").write_text(json.dumps(cohort, indent=2))

    def run_case(cid):
        for pair in range(1, args.pairs + 1):
            for arm in ("off", "on") if pair % 2 else ("on", "off"):
                print(f"Starting {cid} pair {pair} {arm}", flush=True)
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--practice",
                        "--case",
                        cid,
                        "--pair",
                        str(pair),
                        "--arm",
                        arm,
                        "--output",
                        str(root),
                    ],
                    check=True,
                )

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        list(pool.map(run_case, args.cases))
    rows = [json.loads(p.read_text()) for p in sorted(root.glob("*/metrics.json"))]
    (root / "manifest.json").write_text(json.dumps(rows, indent=2))
    print(f"Completed {len(rows)} trials in {root}", flush=True)


if __name__ == "__main__":
    main()
