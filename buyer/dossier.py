"""The supplier dossier: what we know about each fully documented supplier (`detail = full`, 46)
before a case opens, built by `scripts/build_dossier.py` and committed as `buyer/dossier.json` so
the final machine has it and the team can review it before the code freeze.

What the agent uses from it is the `reading` block - situation labels from the reader agent and
the phrase lists, each with the verbatim quote and the document it comes from - absorbed at setup
like any other reading (labels are only ever added, never removed), and its summary as background
for the live reader. Everything else (company, our payments, quality, dependency, costing) is
context for the reader, the team and the jury. The live reader still reads the quote e-mail, a
hidden case's own facts and every reply. A missing or broken file: the agent works as without it.
"""

import json
import logging
from pathlib import Path

from .supplier import SIGNALS

log = logging.getLogger("buyer")
PATH = Path(__file__).with_name("dossier.json")
_DATA = None


def entries():
    global _DATA
    if _DATA is None:
        try:
            data = json.loads(PATH.read_text(encoding="utf-8")).get("suppliers")
            _DATA = data if isinstance(data, dict) else {}
        except (OSError, ValueError, AttributeError) as e:
            log.warning("no supplier dossier (%s): live reading only", e)
            _DATA = {}
    return _DATA


def get(sid):
    entry = entries().get(sid)
    return entry if isinstance(entry, dict) else None


def reading(sid):
    """The dossier's reading in the reader agent's shape, or None (no entry, nothing usable)."""
    entry = get(sid)
    if not entry:
        return None
    r = entry.get("reading") or {}
    signals, evidence = [], {}
    for item in r.get("signals") or []:
        label = item.get("label") if isinstance(item, dict) else None
        if label in SIGNALS and label not in signals:
            signals.append(label)
            evidence[label] = str(item.get("quote") or "")[:200]
    return {
        "signals": signals,
        "asks": [],
        "stance": "unclear",
        "evidence": evidence,
        "summary": str(r.get("summary") or "")[:400],
        "sources": [s for s in r.get("sources") or [] if isinstance(s, str)],
    }


def context(sid):
    """One line of background for the live reader and the writer, or ''."""
    r = reading(sid)
    if not r:
        return ""
    labels = ", ".join(r["signals"]) or "none"
    return f"Our dossier on this supplier: {r['summary']} Situation labels: {labels}."
