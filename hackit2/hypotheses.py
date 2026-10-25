"""Testable negotiation hypotheses from the research team, and how code learns from the supplier.

A hypothesis pairs a move we can make with the reaction we expect from the supplier:

  move      {"kind": "trade", "field": F, "value": V}  offer F=V as an extra sheet that costs us the
                                                       same as our main sheet
            {"kind": "argument", "topic": T}            lead our next e-mail with unused evidence on T
  reaction  "takes_term"  its next reply carries F=V in one of its sheets (trade moves only)
            "price_drop"  its next counter, converted to our planned terms, falls clearly faster
                          than its own previous step: by at least `PRICE_DROP_SHARE` (one
                          percentage point) more. Suppliers concede a little every round anyway,
                          so a plain fall would credit the move with the supplier's own pace.
  p0        the agents' estimate that the reaction follows the move. It is a model estimate, never a
            measured statistic, and hypotheses are not normalised against each other: two
            independent hypotheses may both be likely.

Code checks every move before it can be played (the engine: allowed options, must-haves, a price
at no higher buyer total cost, unused evidence) and plays the most probable valid one. The price,
the numeric terms and accept/walk stay with the engine. After a delivered move, the supplier's next
reply updates the hypotheses that move tested, with fixed likelihood ratios (Bayes in odds form):
supported x SUPPORT_LR, refuted x REFUTE_LR, ambiguous unchanged. The model never writes a new
probability."""

import json
import re

from .prep.briefing import ARGUMENT_TOPICS

REACTIONS = ("takes_term", "price_drop")
SUPPORT_LR = 3.0  # odds factor when the expected reaction is observed
REFUTE_LR = 1 / 3  # odds factor when the reply clearly contradicts it
P_MIN, P_MAX = 0.02, 0.98  # never certain either way: one reply is one observation
PRICE_DROP_SHARE = 0.01  # "it fell faster": its step exceeds its previous step by one percentage point
PRICE_STALL_SHARE = 0.001  # "it did not move": less than 0.1 %
MAX_PER_AGENT = 3
MAX_MERGED = 12

# words a supplier uses for each term, for recognising an explicit refusal in its e-mail
FIELD_WORDS = {
    "payment_days": r"payment|zahlungsziel|pay\b|paid",
    "lead_time_days": r"lead[- ]?time|delivery|lieferzeit",
    "contract_months": r"contract|term\b|laufzeit|duration",
    "volume_per_year": r"volume|quantit|menge",
    "incoterm": r"incoterm|freight|shipping|exw|dap|fca",
    "ppm_commitment": r"ppm|quality|defect",
    "chargeback_eur_per_complaint": r"chargeback|complaint",
    "index_clause": r"index|surcharge",
    "effective_date": r"effective|start date|from (january|february|march|april|may|june|july)",
    "tooling_eur": r"tooling",
}
REFUSAL = r"(cannot|can't|can not|unable to|not able to|won't|will not|do not|don't|not possible|no way)"

CONTRACT = """
Also return "hypotheses": up to three testable moves this supplier's records support, each
{"move": {"kind": "trade", "field": a negotiable non-price field, "value": exactly one of that
 field's term_options} or {"kind": "argument", "topic": one of TOPICS},
 "reaction": "takes_term" (it puts that exact term into one of its sheets; trade moves only) or
 "price_drop" (its next counter falls clearly faster than its previous step, by at least one
 percentage point more),
 "probability": your estimate from 0 to 1 that this reaction follows this move. It is an estimate,
 not a statistic; do not make your hypotheses sum to one,
 "rationale": one sentence why, "source_ids": the supplied source IDs it rests on,
 "uncertainty": what could make it wrong}.
Code prices every trade so it never costs us more than our main proposal; never propose a price.
No supporting source means no hypothesis; an empty list is fine.
""".replace("TOPICS", ", ".join(ARGUMENT_TOPICS))


def clean(raw, sources, case):
    """At most `MAX_PER_AGENT` well-formed hypotheses from one agent's JSON: a known move kind on an
    allowed field and option or argument topic, a known reaction that fits the move, a probability
    in 0..1, and at least one cited source the agent was actually given. Anything else is dropped."""
    out = []
    items = raw.get("hypotheses") if isinstance(raw, dict) else None
    negotiable = [f for f in case.get("negotiable") or [] if f != "unit_price"]
    options = case.get("term_options") or {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("move"), dict):
            continue
        move, reaction = item["move"], item.get("reaction")
        if move.get("kind") == "trade":
            field, value = move.get("field"), move.get("value")
            if field not in negotiable or value not in (options.get(field) or []):
                continue
            move = {"kind": "trade", "field": field, "value": value}
            if reaction not in REACTIONS:
                continue
        elif move.get("kind") == "argument":
            if move.get("topic") not in ARGUMENT_TOPICS or reaction != "price_drop":
                continue
            move = {"kind": "argument", "topic": move["topic"]}
        else:
            continue
        try:
            p = float(item.get("probability"))
        except (TypeError, ValueError):
            continue
        if not 0 <= p <= 1:
            continue
        p = min(P_MAX, max(P_MIN, p))  # a stated certainty would make every later update meaningless
        refs = item.get("source_ids")
        refs = [r for r in refs if isinstance(r, str) and r in sources] if isinstance(refs, list) else []
        if not refs:
            continue
        out.append(
            {
                "move": move,
                "reaction": reaction,
                "p": round(p, 3),
                "rationale": " ".join(str(item.get("rationale") or "").split())[:300],
                "uncertainty": " ".join(str(item.get("uncertainty") or "").split())[:300],
                "source_ids": list(dict.fromkeys(refs)),
            }
        )
        if len(out) >= MAX_PER_AGENT:
            break
    return out


def key_of(h):
    """Two hypotheses are the same when they propose the same move and expect the same reaction."""
    return json.dumps([h["move"], h["reaction"]], sort_keys=True)


def merge(by_agent):
    """One list from every agent's hypotheses: the same move and reaction proposed by several agents
    becomes one hypothesis with all its agents and sources, and the mean of their estimates as its
    starting probability (agreement is not extra evidence: they may read the same records). Ids H1..
    in order of starting probability."""
    groups = {}
    for agent, items in by_agent.items():
        for h in items:
            g = groups.setdefault(
                key_of(h),
                {
                    "move": h["move"],
                    "reaction": h["reaction"],
                    "estimates": [],
                    "agents": [],
                    "source_ids": [],
                    "rationale": [],
                    "uncertainty": [],
                },
            )
            g["estimates"].append(h["p"])
            g["agents"].append(agent)
            g["source_ids"] += [r for r in h["source_ids"] if r not in g["source_ids"]]
            for field in ("rationale", "uncertainty"):
                if h[field] and h[field] not in g[field]:
                    g[field].append(h[field])
    merged = []
    for g in groups.values():
        p0 = round(sum(g["estimates"]) / len(g["estimates"]), 3)
        merged.append({**g, "p0": p0, "p": p0, "agents": sorted(set(g["agents"]))})
    merged.sort(key=lambda h: (-h["p0"], key_of(h)))
    for i, h in enumerate(merged[:MAX_MERGED], 1):
        h["id"] = f"H{i}"
    return merged[:MAX_MERGED]


def start(hypotheses):
    """The thread's own working copy: current probability at its start, no updates, not yet checked."""
    return [
        {**h, "p": h["p0"], "valid": None, "why_not": None, "updates": [], "played": 0}
        for h in hypotheses or []
        if isinstance(h, dict) and h.get("id") and isinstance(h.get("move"), dict)
    ]


def describe(h):
    move = h["move"]
    what = f"offer {move['field']} = {move['value']}" if move["kind"] == "trade" else f"lead with {move['topic']}"
    expect = "it takes that term" if h["reaction"] == "takes_term" else "its counter falls"
    return f"{what} -> {expect}"


def choose(hypotheses):
    """The valid hypothesis with the highest current probability (ties: the earlier id), or None."""
    valid = [h for h in hypotheses if h.get("valid")]
    return min(valid, key=lambda h: (-h["p"], int(h["id"][1:]))) if valid else None


def refused(body, field):
    """True when its e-mail explicitly refuses this term ("we cannot offer shorter payment")."""
    words = FIELD_WORDS.get(field)
    if not words or not body:
        return False
    text = " ".join(body.split())
    return bool(
        re.search(rf"{REFUSAL}[^.!?]{{0,60}}({words})", text, re.I)
        or re.search(
            rf"({words})[^.!?]{{0,60}}(not possible|not acceptable|impossible|no option|out of the question)",
            text,
            re.I,
        )
    )


def observe(h, reply):
    """('supported' | 'refuted' | 'ambiguous', reason) for one hypothesis whose move was in our last
    delivered message, from the supplier's reply. `reply`: sheets (its terms, standing first),
    previous and new counter at our planned terms, status, body, claim results by argument topic
    ("verified" / "contradicted"), its standing value of the trade's field before our move, and
    `previous_step`: the share its counter fell in the round before (None on its first counter: then
    a fall cannot be told from its own pace and is ambiguous). Silence is ambiguous, never a refusal."""
    move = h["move"]
    if h["reaction"] == "takes_term":
        field, value = move["field"], move["value"]
        if any(sheet.get(field) == value for sheet in reply["sheets"]):
            return "supported", f"its reply carries {field} = {value}"
        if refused(reply.get("body"), field):
            return "refuted", f"its e-mail refuses the {field.replace('_', ' ')}"
        standing = reply["sheets"][0].get(field) if reply["sheets"] else None
        before = reply.get("field_before", {}).get(field)
        if standing is not None and before is not None and standing not in (before, value):
            return "refuted", f"it moved {field} to {standing}, not to {value}"
        return "ambiguous", f"its reply neither takes nor refuses {field}"
    previous, new = reply.get("previous_counter"), reply.get("counter")
    if not previous or new is None:
        return "ambiguous", "no comparable counter"
    drop = (previous - new) / previous
    claim = reply.get("claims", {}).get(move.get("topic")) if move["kind"] == "argument" else None
    pace = reply.get("previous_step")
    if pace is not None and drop >= pace + PRICE_DROP_SHARE:
        return "supported", f"its counter fell {100 * drop:.1f} %, against {100 * pace:.1f} % the round before"
    if claim == "contradicted":
        return "refuted", f"the {move['topic']} claim was contradicted"
    if drop < PRICE_STALL_SHARE and (claim == "verified" or reply.get("status") == "final_offer"):
        why = "the claim was verified" if claim == "verified" else "it made its final offer"
        return "refuted", f"its counter did not move although {why}"
    if pace is None:
        return "ambiguous", f"its counter moved {100 * drop:.1f} %; its first step shows no pace to compare with"
    return "ambiguous", f"its counter moved {100 * drop:.1f} %, against {100 * pace:.1f} % the round before"


def update(h, outcome, reason, round_no):
    """Bayes in odds form with the fixed likelihood ratios; an ambiguous reply leaves p unchanged.
    Every update is recorded with its reason."""
    before = h["p"]
    ratio = {"supported": SUPPORT_LR, "refuted": REFUTE_LR}.get(outcome, 1.0)
    odds = before / (1 - before) * ratio
    after = round(min(P_MAX, max(P_MIN, odds / (1 + odds))), 3) if ratio != 1.0 else before
    h["p"] = after
    h["updates"].append({"round": round_no, "outcome": outcome, "reason": reason, "p_before": before, "p_after": after})
    return h
