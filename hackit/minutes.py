"""The minutes of a negotiation: every step our committee and our code took, in order, as JSON.

One file per case in `logs/hackit/`, rewritten after every entry, so even a crashed run leaves its
record. Next to the arena's replay (the supplier's side), the minutes show our side: what each
department read, what the director chose and why, and which numbers code set.
"""

import json
import time
from pathlib import Path


class Minutes:
    def __init__(self, case, log_dir="logs/hackit"):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = Path(log_dir) / f"{case['case_id'].replace(':', '_')}-{stamp}.json"
        self.entries = []
        self.head = {
            "case_id": case["case_id"],
            "title": case.get("title"),
            "type": case.get("type"),
            "goal": case["buyer"].get("category_goal"),
            "started": stamp,
        }

    def add(self, what, **kw):
        self.entries.append({"t": round(time.time(), 1), "what": what, **kw})
        self.save()

    def result(self, result):
        self.head["result"] = result
        self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({**self.head, "entries": self.entries}, ensure_ascii=False, indent=1, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass
