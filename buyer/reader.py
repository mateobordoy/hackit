"""The reader agent: a model reads what a supplier writes and what our data says about it, and
labels its situation from a closed list - the same labels the fixed phrase lists in
`supplier.SIGNALS` and `policy.ASKS` produce, so the rest of the agent uses them unchanged.

Why a model here: the phrase lists found nothing in 143 of 234 practice e-mails, and the Live Final
brings suppliers we have never read ("Faster payment would help us more than anything", "Logistics:
We are well loaded" matched no phrase). Why only here: it labels, code decides. Every label must
come with a sentence quoted from the text; a quote that is not in the text drops the label. The
labels are added to what the phrase lists found, never removed. A late, failing or refused answer
adds nothing, and the move goes on without it.
"""

import hashlib
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from . import config
from .supplier import SIGNALS

log = logging.getLogger("buyer")
_POOL = ThreadPoolExecutor(max_workers=16)
_CLIENT = None
_CACHE = {}
_LOCK = threading.Lock()
STATS = {"calls": 0, "ok": 0, "late": 0, "errors": 0, "dropped_labels": 0}

ASKS = ("volume", "proof", "payment", "lead_time", "contract")
STANCES = ("moving", "holding", "final", "agreeing", "unclear")

GUIDE = """You are the reader on the purchasing team of Hollerau Pumpenwerke, a German pump maker.
You read what an industrial supplier writes (e-mails, call and meeting notes, letters, news, quote
remarks) and label the supplier's situation. You never decide prices; the buyer's system does.

Return only labels the text clearly supports, each with a short verbatim quote from the text (copy
the words exactly, 4-25 words). Greetings, signatures, marketing phrases ("reliable partner",
"extensive range") and the arena's argument checks ("We checked the ... data you cited") carry no
situation: do not label them. When nothing applies, return empty lists. A wrong label is worse
than a missing one.

SIGNALS (the supplier's situation):
- cash_need: it needs money soon - asks to be paid faster, mentions banks, liquidity, losses,
  restructuring, short-time work, overdue cash. "Faster payment would help us more than anything",
  "the bank is watching", "cash is tight". Effect: paying earlier is worth more to it than to us.
- spare_capacity: machines or shifts are idle, it looks for work or new customers. "We have free
  capacity for your parts", "the second shift has been on hold", "looks for new customers".
- capacity_full: it is busy or fully loaded, allocates volumes, prefers customers with firm
  commitments. "Logistics: We are well loaded", "order intake is very strong", "fully booked".
- open_book: it offers to show or walk us through its calculation or cost breakdown.
- stale_costing: its prices rest on an old calculation that has not followed material prices.
  "He works from his own spreadsheet, last updated two years ago".
- partnership_2022: it reminds us it kept supplying at the old price in a past crisis.
- approval_2pct: moves above a small share need a committee or management approval.
- deprioritising_us: it treats us as a small or non-strategic account, narrows its range, supplies
  from stock only.
- price_frozen: price itself will not move (head office froze reductions, list prices for all,
  "no room on price"), though it may still trade terms. "We can talk about the terms instead -
  payment, volume or contract length" alone is not price_frozen unless price is ruled out.
- hedged: it bought its material forward or covered energy at fixed prices, so falling market
  indices do not lower its cost.
- complains_late_pay: it complains that we paid invoices late.

ASKS (what the supplier asks us for, in its own words):
- volume: a binding or higher volume, a volume commitment. "Which volume can you commit to?"
- proof: the data or figures behind our claims. "Which figures is that based on? Please send us the data."
- payment: faster or earlier payment, shorter payment terms.
- lead_time: more time to deliver (a longer lead time). A price start date ("valid from ...") is
  not a lead time.
- contract: a longer contract or a firm commitment period.
An ask is something the supplier requests from us in words ("we need", "please", "would help
us", "if you want a better price, we need ..."). An alternative package it offers ("Alternatively,
with Zahlungsziel 30 days net: EUR ... per unit") or a term it states as its offer is NOT an ask:
the buyer's system already reads every package. When in doubt, leave the ask out.

STANCE (only for an e-mail in an ongoing negotiation; else "unclear"):
- moving: it lowered its price or says it can move further.
- holding: it keeps its price ("our offer stands", "we cannot give up margin right now").
- final: it calls this its final or last offer.
- agreeing: it accepts our terms.

summary: one plain sentence on the supplier's situation and what would move it, without figures.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "enum": sorted(SIGNALS)},
                    "quote": {"type": "string"},
                },
                "required": ["label", "quote"],
                "additionalProperties": False,
            },
        },
        "asks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "enum": list(ASKS)},
                    "quote": {"type": "string"},
                },
                "required": ["label", "quote"],
                "additionalProperties": False,
            },
        },
        "stance": {"type": "string", "enum": list(STANCES)},
        "summary": {"type": "string"},
    },
    "required": ["signals", "asks", "stance", "summary"],
    "additionalProperties": False,
}


_BROKEN = threading.Event()  # the key is out of credit or refused: stop calling for this run


def account_failure(error):
    """A key out of credit or refused will not recover within the case: stop calling the model."""
    text = str(error).lower()
    if any(k in text for k in ("usage limit", "credit balance", "authentication", "invalid x-api-key", "permission")):
        if not _BROKEN.is_set():
            log.warning("model account unusable, running without the model agents: %s", error)
        _BROKEN.set()


def enabled():
    return (
        not _BROKEN.is_set()
        and bool(os.environ.get("LLM_MODEL"))
        and os.environ.get("LLM_PROVIDER", "anthropic") == "anthropic"
        and os.environ.get("READER", "1") != "0"
    )


def _client():
    global _CLIENT
    if _CLIENT is None:
        import anthropic

        _CLIENT = anthropic.Anthropic(timeout=config.READER_TIMEOUT_S, max_retries=1)
    return _CLIENT


def _norm(text):
    return re.sub(r"[^a-z0-9äöüß%]+", " ", (text or "").lower()).strip()


def validate(raw, text):
    """Keep the labels whose quote really is in the text."""
    hay = _norm(text)
    out = {"signals": [], "asks": [], "evidence": {}, "stance": raw.get("stance") or "unclear"}
    for kind, allowed in (("signals", set(SIGNALS)), ("asks", set(ASKS))):
        for item in raw.get(kind) or []:
            label, quote = item.get("label"), _norm(item.get("quote"))
            if label not in allowed or len(quote) < 8 or quote not in hay:
                STATS["dropped_labels"] += 1
                continue
            if label not in out[kind]:
                out[kind].append(label)
                out["evidence"][label] = item.get("quote", "")[:200]
    if out["stance"] not in STANCES:
        out["stance"] = "unclear"
    out["summary"] = re.sub(r"\d", "", str(raw.get("summary") or ""))[:300]
    return out


def _call(kind, text, context=""):
    STATS["calls"] += 1
    content = f"WHAT YOU READ ({kind}):\n{text[: config.READER_MAX_CHARS]}"
    if context:
        # background from our dossier: it helps to interpret, but every label and quote must come
        # from WHAT YOU READ (validate() checks the quote against that text only)
        content = f"BACKGROUND (our dossier; do not quote it, do not label from it):\n{context}\n\n{content}"
    r = _client().messages.create(
        model=os.environ["LLM_MODEL"],
        max_tokens=4000,
        system=GUIDE,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"reader stopped: {r.stop_reason}")
    body = next(b.text for b in r.content if b.type == "text")
    return validate(json.loads(body), text)


def submit(kind, text, context=""):
    """Start reading in the background; a future, or None when off or nothing to read."""
    if not enabled() or not (text or "").strip():
        return None
    key = hashlib.sha1(f"{kind}\n{context}\n{text}".encode()).hexdigest()
    with _LOCK:
        if key not in _CACHE:
            _CACHE[key] = _POOL.submit(_call, kind, text, context)
        return _CACHE[key]


def result(future, budget_s):
    """What the reader found, or None when it is late or failed."""
    if future is None or budget_s <= 0:
        return None
    try:
        out = future.result(timeout=budget_s)
    except FutureTimeout:
        STATS["late"] += 1
        return None
    except Exception as e:  # noqa: BLE001 - the reader must never cost the move
        STATS["errors"] += 1
        log.warning("reader failed: %s", e)
        account_failure(e)
        return None
    STATS["ok"] += 1
    return out


def read(kind, text, budget_s, context=""):
    return result(submit(kind, text, context), budget_s)
