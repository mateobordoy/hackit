"""Memory across cases: before a case the lead researcher sees what we did in the most similar earlier
cases and how it ended (the figures, not the lesson text: one run is a data point, not a rule).

Rows are keyed by features (goal, case type, ownership, traits), never by a case or supplier ID. The
committed `lessons.jsonl` holds them:
  goal, type, bidders, owners, signals, deal, score, lesson,
  threads: [{opened, ours, theirs, tactics, trades}] (prices as shares of the quote)"""

import json
from pathlib import Path

from . import settings, telemetry

LESSONS_FILE = Path(__file__).parent / "lessons.jsonl"  # committed, so the final machine starts with it


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
