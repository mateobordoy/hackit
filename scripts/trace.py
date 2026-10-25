"""Print the decision trace of run logs: python scripts/trace.py [folder] [case filter]."""

import json
import sys
from pathlib import Path

folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/runs")
flt = sys.argv[2] if len(sys.argv) > 2 else ""
for p in sorted(folder.glob("*.json")):
    if flt and flt not in p.name:
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    r = d["result"]
    print(f"\n=== {d['case']} {d['session']} score={r.get('score')} deal={r.get('deal')} terms={r.get('terms')}")
    for n in d["notes"]:
        w = n["what"]
        if w == "setup":
            print(
                f"  setup {n['supplier']}: q0={n['q0']} anchor={n['anchor']} target={n['target']} "
                f"prior={n['prior']} rate={n['rate']} signals={n['signals']} claims={n['claims']}"
            )
            print(f"        terms={n['terms']}")
        elif w == "send":
            print(
                f"  send  {n['supplier']}: {n['price']} (theirs {n['theirs']}) sheets={n['sheets']} "
                f"claims={n['claims']} mirror={n['mirror']}"
            )
        elif w == "reply":
            print(f"  reply {n['supplier']}: {n['status']} {n['theirs']} gap={n['gap_sum']} belief={n['belief']}")
        elif w == "planner":
            print(f"  plan  {n['supplier']}: -> {n['price']} E[C]={n['expected']} table={n['table']}")
        else:
            rest = {k: v for k, v in n.items() if k not in ("t", "supplier", "what")}
            print(f"  {w} {n['supplier']}: {rest}")
