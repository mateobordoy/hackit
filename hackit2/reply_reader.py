"""The reply reader: looks at every supplier e-mail as it arrives (the opening quote included) and
tells the negotiator the writer's mood and the tactic for our next move, so the bidding follows the
supplier we actually face and not only the one in our files. It also matches the e-mail against
the arguments the sparring round predicted, so the e-mail writer gets our prepared answer.

Every value it returns comes from a fixed list and the negotiation engine decides what each value
changes. Traits that move anchors or rates stay with the patterns in `supplier_profile`: a model's
guess there is too expensive."""

from . import model, settings, telemetry, transcript

MOODS = ("warm", "neutral", "irritated", "pressured")
TACTICS = ("steady", "hold", "argue")
REQUEST_KINDS = ("volume", "proof", "payment", "target")  # the kinds `negotiation.thread.detect_requests` finds

SYSTEM = """You sit next to our buyer at Hollerau Pumpenwerke during a negotiation as the analyst. Read the
supplier's latest e-mail and tell us who we are dealing with at this moment and what to do next.
Return JSON only:
{"mood": the mood of the person writing, one of "warm", "neutral", "irritated", "pressured",
 "tactic": our best next move, one of:
   "hold" - it is still conceding or signals room to move: we move less than usual,
   "argue" - it raises a point we can answer or doubts our data: we answer with evidence,
   "steady" - neither of these,
 "argument_no": the number of the EXPECTED ARGUMENT this e-mail makes, or 0 if none,
 "requests": what it asks of us, only from this list: volume, proof, payment, target,
 "notes": one or two sentences for the colleague who writes our reply: what this person cares
  about right now and how to answer. No figures, no dates.}"""


@telemetry.traced("reply_reader.read_reply")
def read_reply(
    complete, case, body, our_prices, their_prices, expected_arguments=(), timeout=None, history=None, supplier_id=None
):
    """{mood, tactic, argument, requests, notes} for one supplier e-mail, every value clamped to its
    list; None when the model is off, late or wrong. `expected_arguments` are what the sparring
    round predicted; `argument` indexes them from 1 (0 for none)."""
    timeout = settings.READER_TIMEOUT_S if timeout is None else timeout
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(expected_arguments, 1)) or "none prepared"
    user = (
        f"CASE: {case.get('title')} ({case.get('type')}, goal {case['buyer'].get('category_goal')})\n"
        f"OUR PRICES SO FAR: {our_prices}\nITS PRICES SO FAR: {their_prices}\nEXPECTED ARGUMENTS:\n{numbered}\n"
        f"ITS E-MAIL:\n{(body or '')[:2500]}"
    )
    if history is not None:
        user += transcript.as_prompt(history)
    raw = model.ask_json(
        complete, SYSTEM, user, min(timeout, settings.READER_TIMEOUT_S), role="reply_reader", supplier_id=supplier_id
    )
    if raw is None:
        return None
    requests = raw.get("requests")
    number = raw.get("argument_no")
    return {
        "mood": raw.get("mood") if raw.get("mood") in MOODS else "neutral",
        "tactic": raw.get("tactic") if raw.get("tactic") in TACTICS else "steady",
        "argument": number if isinstance(number, int) and 0 < number <= len(expected_arguments) else 0,
        "requests": [r for r in requests if r in REQUEST_KINDS] if isinstance(requests, list) else [],
        "notes": " ".join(str(raw.get("notes") or "").split())[:400],
    }
