"""Learning across cases. When a scored case closes, a debrief model reads how it went and writes one
lesson; before the next case the lead researcher sees what we did in the most similar earlier cases
and how it ended (the figures, not the lesson text: one run is a data point, not a rule).

Rows are keyed by features (goal, case type, ownership, traits), never by a case or supplier ID, and
code writes every figure in them. The file format is shared with the seeded `lessons.jsonl`:
  goal, type, bidders, owners, signals, deal, score, lesson,
  threads: [{opened, ours, theirs, tactics, trades}] (prices as shares of the quote)"""

import json
import logging
import threading
from pathlib import Path

from . import model, settings, telemetry

log = logging.getLogger("hackit2")
LESSONS_FILE = Path(__file__).parent / "lessons.jsonl"  # committed, so the final machine starts with it
_write_lock = threading.Lock()

DEBRIEF_SYSTEM = """You write the debrief for the purchasing team of Hollerau Pumpenwerke. Below is how one
negotiation went, expressed in shares of the supplier's quote: where we opened, our price path, its
price path, its reasons in its own words, and the score (1.0 is the best deal that was possible, 0 is
no better than its first offer).
Our team can only change four things: which terms it offers as an alternative next to its main
proposal, which kind of argument it cites first, how it answers the supplier's arguments, and its
tone. Code sets prices and pace. Threats, deadlines, bluffs and requests for cost data have been
tested and do not work; never suggest them.
Write the single lesson for the next negotiation with this KIND of supplier (ownership, traits,
goal): what its path and reasons show about how it concedes and when it stops, and which of the four
things to change, or to keep if the score is high.
Never name the supplier, the part, the case or any ID, and give no absolute prices.
Return JSON only: {"lesson": "one or two short sentences"}"""


def case_features(case, threads):
    return {
        "goal": case["buyer"].get("category_goal"),
        "type": case.get("type"),
        "bidders": len(threads),
        "owners": sorted({t.profile.get("ownership") or "unknown" for t in threads}),
        "signals": sorted({trait for t in threads for trait in t.profile.get("traits", [])}),
    }


def price_path(thread):
    """One thread in shares of its quote, comparable across cases."""
    quote = thread.quote_at_plan or 1.0
    return {
        "opened": round(thread.our_prices[0] / quote, 3),
        "ours": [round(p / quote, 3) for p in thread.our_prices],
        "theirs": [round(p / quote, 3) for p in thread.their_prices],
        "tactics": thread.tactic_log,
        "trades": [f"{t['field']}={t['value']}" for t in thread.prep_trades],
    }


def supplier_reasons(result, supplier_id):
    """The supplier's stated reasons round by round, from the replay trace (practice and sandbox)."""
    reasons = []
    for step in result.get("trace") or []:
        if step.get("actor") == supplier_id and step.get("reasons"):
            reasons.append(f"round {step.get('round')}: " + " ".join(str(r) for r in step["reasons"])[:400])
    return reasons


@telemetry.traced("lessons.record_lesson")
def record_lesson(case, negotiator, result, complete):
    """Append this case's row with its lesson. Only a scored case teaches (the final shows no score)."""
    if result.get("score") is None:
        return None
    threads = [t for t in negotiator.suppliers.values() if t.our_prices]
    if not threads:
        return None
    winner = negotiator.suppliers.get(result.get("supplier_id"))
    row = {
        **case_features(case, threads),
        "deal": bool(result.get("deal")),
        "score": round(result["score"], 3),
        "threads": [price_path(t) for t in ([winner] if winner and winner.our_prices else threads)],
    }
    reasons = [r for t in threads for r in supplier_reasons(result, t.supplier_id)][-12:]
    prompt = json.dumps({**row, "reasons": reasons}, ensure_ascii=False)
    answer = model.ask_json(complete, DEBRIEF_SYSTEM, prompt, role="debrief") or {}
    row["lesson"] = " ".join(str(answer.get("lesson") or "").split())[:300]
    with _write_lock, LESSONS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


@telemetry.traced("lessons.recall_lessons", result_view=lambda lines: f"{len(lines)} similar earlier cases")
def recall_lessons(case, count=None):
    """The latest rows of the most similar earlier cases (same goal, then same case type) as lines:
    what we did and what came of it. The lesson text itself stays in the file, since a lead reads one
    run's story as a rule."""
    count = settings.LESSONS_RECALLED if count is None else count
    if not LESSONS_FILE.is_file():
        return []
    rows = []
    for line in LESSONS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    goal, kind = case["buyer"].get("category_goal"), case.get("type")
    ranked = sorted(
        enumerate(rows), key=lambda item: ((item[1].get("goal") == goal) + (item[1].get("type") == kind), item[0])
    )
    lines = []
    for _, row in ranked[-count:]:
        if row.get("goal") != goal and row.get("type") != kind:
            continue
        first = (row.get("threads") or [{}])[0]
        lines.append(
            f"{row.get('goal')} {row.get('type')}, {row.get('bidders')} bidder(s), owner "
            f"{'/'.join(row.get('owners') or [])}, traits {row.get('signals')}: opened at {first.get('opened')} of "
            f"the quote, offered trades {first.get('trades') or []}, its prices {first.get('theirs')}, "
            f"score {row.get('score')}."
        )
    return lines
