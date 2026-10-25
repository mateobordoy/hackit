"""The writer agent: one short paragraph per e-mail answering the supplier's last words, written
with what the reader agent found about the supplier (its situation, what it asks, its stance).

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

SYSTEM = """You write ONE or TWO short sentences (at most forty words) for a buyer's e-mail to an
industrial supplier. The buyer is Hollerau Pumpenwerke, a German pump maker. The e-mail already
thanks the supplier, says we value the partnership and understand its cost pressure, and the
buyer's system adds the figures, the data and the proposal; do not repeat any of that. Your
sentences answer what is specific in the supplier's last e-mail.

How to write it:
- Name its one or two most important points in your own words and show you understood them.
  Businesslike and warm; no flattery, no praise of its craftsmanship.
- Use the READER NOTES: if the supplier needs cash, say that the proposal takes its payment needs
  into account; if it asked for volume, a contract or proof, say the proposal and the attached
  data answer that; if it is busy, show respect for its planning; if its price is frozen, say we
  are open to finding value in the terms. If it holds its price, acknowledge that calmly and invite
  it to look at the package as a whole.
- Refer only to what CONTEXT lists under "what our proposal contains"; if the supplier asked for
  something the proposal does not contain, say kindly that we looked at it and point to what we
  do offer.
- On the opening quote there is no stance yet: do not say it holds or moves.

Strict rules: no digits at all, no data IDs, no prices, no dates, no greeting and no sign-off.
Never mention deadlines, budgets, urgency, other suppliers, alternatives, walking away or targets.
Never promise anything the proposal does not contain. Reply with the sentences only."""


def enabled():
    from .reader import _BROKEN

    return bool(os.environ.get("LLM_MODEL")) and not _BROKEN.is_set()


def _complete():
    global _COMPLETE
    if _COMPLETE is None:
        if os.environ.get("LLM_PROVIDER", "anthropic") == "anthropic":
            import anthropic

            client = anthropic.Anthropic(timeout=config.LLM_TIMEOUT_S, max_retries=0)

            def complete(system, user):
                r = client.messages.create(
                    model=os.environ["LLM_MODEL"],
                    max_tokens=4000,
                    system=system,
                    output_config={"effort": "low"},
                    messages=[{"role": "user", "content": user}],
                )
                if r.stop_reason != "end_turn":
                    return ""
                return "".join(b.text for b in r.content if b.type == "text")

            _COMPLETE = complete
        else:
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
        from .reader import account_failure

        account_failure(e)
        return None
    safe = safe_paragraph(text)
    if safe is None:
        STATS["rejected"] += 1
        return None
    STATS["ok"] += 1
    return safe


def prompt(case, prof, last_body, notes, reading=None):
    who = {"family": "a family-owned company", "group": "part of a larger group", "founder": "a founder-run shop"}.get(
        prof.get("owner_type"), "an industrial supplier"
    )
    lines = [
        f"CASE: {case.get('title')}",
        f"SUPPLIER: {who}.",
        f"ITS LAST E-MAIL:\n{(last_body or '')[:1800]}",
    ]
    if reading:
        lines.append(
            "READER NOTES (never quote them): "
            f"situation: {reading.get('summary') or '-'}; "
            f"signals: {', '.join(reading.get('signals') or []) or 'none'}; "
            f"it asks for: {', '.join(reading.get('asks') or []) or 'nothing'}; "
            f"stance: {reading.get('stance') or 'unclear'}"
        )
    if notes:
        lines.append("CONTEXT (never quote it): " + "; ".join(notes))
    return "\n".join(lines)
