"""Run logs: one JSON per session (every decision with the belief behind it, and the result), and
a report over them - per case mean and best score, next to the ProcureCabana reference."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

from . import config

RUNS = config.LOG_DIR / "runs"
KEEP = ("deal", "supplier_id", "terms", "c_open", "c_deal", "c_alt", "c_best", "score", "end_reason", "cost_breakdown")
# ProcureCabana's best practice score per hosted case (README) and its last ten-case batch
# (docs/learnings.md, 2026-10-11): what "better than the winner" means on practice.
REFERENCE_BEST = {
    "case:P01": 0.999,
    "case:P02": 0.735,
    "case:P03": 0.933,
    "case:P04": 0.860,
    "case:P05": 0.828,
    "case:P07": 0.760,
    "case:P08": 0.748,
    "case:P10": 0.710,
    "case:P11": 0.794,
    "case:P18": 0.756,
}
REFERENCE_BATCH = {
    "case:P01": 0.9085,
    "case:P02": 0.7347,
    "case:P03": 0.9276,
    "case:P04": 0.8604,
    "case:P05": 0.8218,
    "case:P07": 0.7313,
    "case:P08": 0.7036,
    "case:P10": 0.6785,
    "case:P11": 0.7099,
    "case:P18": 0.6957,
}


def save(session_id, case, neg, result, folder=None):
    folder = Path(folder or RUNS)
    folder.mkdir(parents=True, exist_ok=True)
    cid = case.get("case_id", "case:?")
    out = {
        "case": cid,
        "set": case.get("set"),
        "type": case.get("type"),
        "goal": case["buyer"].get("category_goal"),
        "session": session_id,
        "result": {k: (result or {}).get(k) for k in KEEP},
        "notes": neg.notes,
    }
    path = folder / f"{cid.split(':')[-1]}_{session_id}.json"
    path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return path


def load(folder=None, set_=None):
    rows = []
    for p in sorted(Path(folder or RUNS).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if set_ and d.get("set") not in (set_, None):
            continue
        rows.append(d)
    return rows


def report(folder=None, set_="practice"):
    """A plain-text table: per case n, mean, best, the reference, and the totals."""
    by_case = defaultdict(list)
    for d in load(folder, set_):
        score = (d.get("result") or {}).get("score")
        if score is not None:
            by_case[d["case"]].append(score)
    if not by_case:
        return "no finished runs in logs/runs"
    lines = [f"{'case':10} {'n':>2} {'mean':>6} {'best':>6} {'PC batch':>8} {'PC best':>8}  scores"]
    means, bests, refs_b, refs_x = [], [], [], []
    for cid in sorted(by_case):
        s = by_case[cid]
        m, b = statistics.mean(s), max(s)
        rb, rx = REFERENCE_BATCH.get(cid), REFERENCE_BEST.get(cid)
        means.append(m)
        bests.append(b)
        if rb is not None:
            refs_b.append(rb)
            refs_x.append(rx)
        lines.append(
            f"{cid:10} {len(s):>2} {m:6.3f} {b:6.3f} {rb if rb is not None else '-':>8} "
            f"{rx if rx is not None else '-':>8}  {', '.join(f'{x:.3f}' for x in s)}"
        )
    lines.append(
        f"{'total':10} {'':>2} {100 * statistics.mean(means):6.1f} {100 * statistics.mean(bests):6.1f} "
        f"{100 * statistics.mean(refs_b) if refs_b else 0:8.1f} {100 * statistics.mean(refs_x) if refs_x else 0:8.1f}"
    )
    return "\n".join(lines)
