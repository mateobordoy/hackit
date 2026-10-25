"""Learning between cases (empirical Bayes): where the suppliers of this set really stopped.

After every case each supplier thread that reached its last word (a final offer or an agreement)
leaves one number: its last price at our terms as a share of its opening quote - the same quantity
the factory priors in `config.FLOOR_PRIOR` were measured in on practice. Before the next case of
the same type, the factory prior's mean is pulled toward what this set showed:

    mean = (N0 * factory + n * observed) / (N0 + n), the shift capped at MAX_SHIFT

With one or two cases the factory prior still weighs most; in a Live Final it is the suppliers of
waves 1-2 that inform waves 3-5. The pull only ever goes up (more careful): a lower floor prior
makes the planner step less, and these suppliers concede in proportion to our step, so it finalled
earlier and higher (P18 0.756 in three runs, 0.688 after a 1.5-point lower prior; the higher priors
of the same batch changed nothing). The spread only ever widens (a population more varied than
practice gets a wider prior, never a narrower one). Rows are keyed on the case type and set, never
on a supplier or case ID, so they carry over to suppliers we have not met. Any error: no shift.

Unlike a lesson that needs the score (the final shows none), the stopping price is in every thread.
"""

import json
import logging
import statistics
import threading
import time

from . import config

log = logging.getLogger("buyer")
_LOCK = threading.Lock()
DONE = ("final_offer", "agreed")


def path(set_):
    return config.LOG_DIR / f"memory_{set_ or 'unknown'}.jsonl"


def record(case, neg, result=None):
    """Append where each supplier of this case stopped: its last word (final offer, agreement) or
    the sheet we accepted from it. Returns the rows written."""
    rows = []
    won = (result or {}).get("supplier_id") if (result or {}).get("deal") else None
    for sid, t in neg.threads.items():
        if not t.ours or not t.q0 or not t.theirs or (t.last_status not in DONE and sid != won):
            continue
        share = t.theirs[-1] / t.q0
        if not 0.5 < share <= 1.05:
            continue
        rows.append(
            {
                "set": case.get("set"),
                "type": case.get("type"),
                "goal": case["buyer"].get("category_goal"),
                "share": round(share, 4),
                "status": t.last_status if t.last_status in DONE else "accepted",
                "rounds": len(t.ours),
                "signals": sorted(t.prof.get("signals") or []),
                "at": round(time.time()),
            }
        )
    if rows:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK, path(case.get("set")).open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return rows


def observed(set_, kind):
    """The stopping shares this set showed for this case type so far."""
    p = path(set_)
    if not p.is_file():
        return []
    out = []
    with _LOCK:
        lines = p.read_text(encoding="utf-8").splitlines()
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") == kind and isinstance(r.get("share"), (int, float)):
            out.append(float(r["share"]))
    return out


def adjust(case, mean, sd):
    """(mean, sd, note) of the floor prior after what this set showed; unchanged without data."""
    try:
        kind = case.get("type")
        factory = config.FLOOR_PRIOR.get(kind, config.FLOOR_PRIOR_DEFAULT)[0]
        obs = observed(case.get("set"), kind)
        if not obs:
            return mean, sd, None
        n = len(obs)
        pooled = (config.MEMORY_N0 * factory + n * statistics.fmean(obs)) / (config.MEMORY_N0 + n)
        shift = max(-config.MEMORY_MAX_DOWN, min(config.MEMORY_MAX_SHIFT, pooled - factory))
        new_sd = sd
        if n >= 3:
            new_sd = max(sd, min(config.MEMORY_MAX_SD, statistics.pstdev(obs)))
        new_mean = min(0.995, max(0.5, mean + shift))
        return new_mean, new_sd, {"n": n, "observed": round(statistics.fmean(obs), 4), "shift": round(shift, 4)}
    except Exception:  # noqa: BLE001 - memory must never cost a case
        log.exception("memory skipped")
        return mean, sd, None
