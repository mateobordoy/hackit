"""The optional model: one short paragraph per e-mail answering the supplier's last words.

Off unless LLM_MODEL is set. It never sees a decision it could change: prices, terms and claims
are fixed before it is called. A late, failing or unsafe answer is dropped and the template
paragraph is used; the move is never delayed past its budget. A fully model-written e-mail
scored the same as the template on practice, so the model is a polish, not a lever. [pc]
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from . import config
from .writer import safe_paragraph

log = logging.getLogger("buyer")
_POOL = ThreadPoolExecutor(max_workers=32)
_COMPLETE = None
STATS = {"calls": 0, "ok": 0, "late": 0, "rejected": 0, "errors": 0}

SYSTEM = """You write ONE short paragraph (2-4 sentences) for a buyer's e-mail to an industrial
supplier. The buyer is Hollerau Pumpenwerke, a German pump maker. Answer the supplier's last
e-mail politely and specifically: acknowledge its points, show understanding, keep the
relationship warm, and invite a constructive next step. The figures, data and the proposal are
added after your paragraph by the buyer's system - do not repeat or invent them.
Strict rules: no digits at all, no data IDs, no prices, no dates, no greeting and no sign-off.
Never mention deadlines, budgets, urgency, other suppliers, alternatives, walking away or targets.
Reply with the paragraph only."""


def enabled():
    return bool(os.environ.get("LLM_MODEL"))


def _complete():
    global _COMPLETE
    if _COMPLETE is None:
        from arena_kit.agents.llm import llm_from_env

        _COMPLETE = llm_from_env()
    return _COMPLETE


def paragraph(prompt, budget_s):
    """A safe paragraph or None, within `budget_s` seconds."""
    if not enabled() or budget_s < config.LLM_MIN_S:
        return None
    STATS["calls"] += 1
    try:
        future = _POOL.submit(_complete(), SYSTEM, prompt)
        text = future.result(timeout=min(budget_s, config.LLM_TIMEOUT_S))
    except FutureTimeout:
        STATS["late"] += 1
        return None
    except Exception as e:  # noqa: BLE001 - the model must never cost the move
        STATS["errors"] += 1
        log.warning("model failed: %s", e)
        return None
    safe = safe_paragraph(text)
    if safe is None:
        STATS["rejected"] += 1
        return None
    STATS["ok"] += 1
    return safe


def prompt(case, prof, last_body, notes):
    who = {"family": "a family-owned company", "group": "part of a larger group", "founder": "a founder-run shop"}.get(
        prof.get("owner_type"), "an industrial supplier"
    )
    lines = [
        f"CASE: {case.get('title')}",
        f"SUPPLIER: {who}.",
        f"ITS LAST E-MAIL:\n{(last_body or '')[:1800]}",
    ]
    if notes:
        lines.append("CONTEXT (never quote it): " + "; ".join(notes))
    return "\n".join(lines)
