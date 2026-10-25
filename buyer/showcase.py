"""The Showcase: one real, recorded buyer interaction as a storyboard the page plays step by step for
the jury (`GET /api/showcase`, the page's Showcase button; contract in AGENT_SHOWCASE.md).

A run is the event list of one live-log segment (`telemetry.py`). Bundled runs in `buyer/showcase/`
(frozen with `buyer showcase --save <session>`) make it work after the Live Final and offline. The
story follows one main thread (the supplier we signed with, else the longest thread) round by round;
the other bidders appear when they open, settle or are dropped. Every caption is built from the
recorded numbers: nothing is invented here."""

import json
import math
from pathlib import Path

BUNDLED = Path(__file__).with_name("showcase")
DEFAULT = "default"
HIDE_SIM = True  # arena-sim runs (scripts/sim_demo.py, sessions "sim1"...) are test plumbing, not for the jury
GUARDS = {
    "hold_after_deep_drop": "Deep first drop: hold the anchor",
    "probe_sheet": "Probe package: what is money worth to it?",
    "need_sheet": "A package for what it asked",
    "rate_learned": "Cost of money learned from its pick",
    "switch_terms": "Terms switched",
    "clause_trade": "Index clause trade",
    "dominated": "Bidder dominated: stop spending rounds",
    "at_ceiling": "At our ceiling: never above the alternative",
    "at_relationship_target": "Relationship target reached",
    "mirror": "Mirror its cheaper sheet",
    "last_sheet": "One last sheet at our ceiling",
    "rollback": "Late message rolled back",
    "refused": "Arena refused a sheet: thread closed",
    "planner_accept": "Accept now: going on is worth less",
    "no_real_step": "No token move: a real step or none",
    "no_progress": "Nothing new to offer: thread settled",
    "keepalive": "Keepalive: settled bidder kept warm",
}
DONE = ("final_offer", "agreed")


def eur(x, digits=2):
    try:
        return f"€{float(x):,.{digits}f}"
    except (TypeError, ValueError):
        return "–"


def pct(x, digits=1):
    try:
        return f"{100 * float(x):.{digits}f} %"
    except (TypeError, ValueError):
        return "–"


def clip(text, n=1500):
    text = text or ""
    return text if len(text) <= n else text[:n] + " …"


def read_jsonl(path):
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def bundled():
    """{name: events} of the frozen runs shipped with the agent."""
    if not BUNDLED.is_dir():
        return {}
    return {p.stem: read_jsonl(p) for p in sorted(BUNDLED.glob("*.jsonl"))}


def head_of(events):
    return next((e for e in events if e.get("event") == "case_start"), {})


def result_of(events):
    end = next((e for e in reversed(events) if e.get("event") == "end"), None)
    return (end or {}).get("result") or {}


def save(events, name=DEFAULT, folder=None):
    """Freeze one run as a bundled showcase. Returns the path."""
    folder = Path(folder or BUNDLED)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    path.write_text("".join(json.dumps(e, default=str, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return path


# -- the story ------------------------------------------------------------------------------------------
def notes_of(events, sid=None, what=None):
    return [
        e
        for e in events
        if e.get("event") == "note"
        and (sid is None or e.get("supplier") == sid)
        and (what is None or e.get("what") == what)
    ]


def main_supplier(events, suppliers):
    won = result_of(events).get("supplier_id")
    if won in suppliers:
        return won
    sends = {s: len(notes_of(events, s, "send")) for s in suppliers}
    return max(suppliers, key=lambda s: sends.get(s, 0)) if suppliers else None


def rounds(events, sid, q0):
    """Round 0 is its quote; round r is our r-th sheet and its answer, prices in EUR at our terms."""
    out, ours = [], []
    setup = (notes_of(events, sid, "setup") or [{}])[0]
    mean, sd = (setup.get("prior") or [None, None])[:2]
    first = {"round": 0, "ours": None, "theirs": q0, "status": "quote"}
    if q0 and mean is not None and sd is not None:
        first.update(p10=q0 * (mean - 1.2816 * sd), p50=q0 * mean, p90=q0 * min(1.0, mean + 1.2816 * sd))
    out.append(first)
    for e in events:
        if e.get("event") != "note" or e.get("supplier") != sid:
            continue
        if e.get("what") == "send" and not e.get("mirror"):
            ours.append(e.get("price"))
        elif e.get("what") == "reply" and ours:
            b = e.get("belief") or {}
            row = {"round": len(ours), "ours": ours[-1], "theirs": e.get("theirs"), "status": e.get("status")}
            if q0:
                row.update({k: q0 * b[f"floor_{k}"] for k in ("p10", "p50", "p90") if b.get(f"floor_{k}") is not None})
            if out[-1]["round"] == row["round"]:
                out[-1] = row
            else:
                out.append(row)
    return out


def supplier_card(events, sid):
    setup = (notes_of(events, sid, "setup") or [{}])[0]
    readers = notes_of(events, sid, "reader")
    memory = (notes_of(events, sid, "memory") or [None])[0]
    quote = setup.get("quote") or {}
    return {
        "id": sid,
        "q0": setup.get("q0"),
        "quote_price": quote.get("unit_price"),
        "anchor": setup.get("anchor"),
        "target": setup.get("target"),
        "prior": setup.get("prior"),
        "signals": setup.get("signals") or [],
        "owner": setup.get("owner"),
        "rate": setup.get("rate"),
        "full_cost": setup.get("full_cost"),
        "claims": setup.get("claims") or [],
        "terms": setup.get("terms"),
        "std": setup.get("std"),
        "reader": readers[0] if readers else None,
        "memory": memory,
    }


def step(kind, title, caption, flow=(), supplier=None, rnd=None, **data):
    return {
        "kind": kind,
        "title": title,
        "caption": caption,
        "supplier": supplier,
        "round": rnd,
        "flow": [list(f) for f in flow],
        "data": data,
    }


def intro_step(head, suppliers, result):
    need, years = head.get("need_per_year"), head.get("horizon_years")
    volume = f"{need:,} units a year for {years} years" if need and years else "the yearly need"
    alt = head.get("c_alt") or result.get("c_alt")
    caption = (
        f"{head.get('title') or 'A sourcing case'}. Hollerau buys {volume}. "
        f"{len(suppliers)} supplier{'s' if len(suppliers) != 1 else ''} quoted"
        + (f"; the alternative would cost {eur(alt, 0)} in total" if alt else "")
        + f". Category goal: {head.get('goal') or '–'}."
        + " The buyer negotiates alone, by e-mail, with no human in the loop."
    )
    return step(
        "intro",
        "The case",
        caption,
        [("arena", "runner")],
        case_id=head.get("case_id"),
        case_title=head.get("title"),
        type=head.get("type"),
        goal=head.get("goal"),
        suppliers=suppliers,
        c_alt=alt,
        need_per_year=need,
        horizon_years=years,
        current_unit_price=head.get("current_unit_price"),
        negotiable=head.get("negotiable"),
    )


def prep_step(card):
    sid, q0, anchor = card["id"], card["q0"], card["anchor"]
    mean, sd = (card["prior"] or [None, None])[:2]
    parts = [f"{card['owner']}-owned supplier" if card["owner"] else "supplier with no owner data"]
    if card["signals"]:
        parts.append("signals " + ", ".join(card["signals"]))
    if card["rate"]:
        parts.append(f"money costs it about {pct(card['rate'])} a year")
    caption = f"{sid}: " + "; ".join(parts) + ". "
    if q0 and anchor:
        caption += f"Its quote is {eur(q0)} at our planned terms; we will open at {eur(anchor)} ({pct(anchor / q0)})"
        caption += f" with {len(card['claims'])} verified data claims. " if card["claims"] else ". "
    if mean is not None:
        caption += f"Prior belief: its floor sits near {pct(mean)} of the quote (± {pct(sd)})."
    memory = card.get("memory")
    if memory and memory.get("shift"):
        caption += f" Memory from {memory.get('n')} earlier stops moved it by {pct(memory['shift'])}."
    flow = [("supplier", "policy"), ("reader", "policy"), ("evidence", "policy"), ("economics", "policy")]
    if memory:
        flow.append(("memory", "belief"))
    return step("prep", f"Preparation · {sid}", caption, flow, supplier=sid, card=card)


def send_caption(sid, note, msg, first):
    price, claims = note.get("price"), note.get("claims") or []
    sheets = note.get("sheets") or len((msg or {}).get("term_sheets") or [])
    text = f"{'Opening e-mail' if first else 'Next e-mail'} to {sid}: {eur(price)} per unit"
    if sheets and sheets > 1:
        text += f" in {sheets} packages"
    if claims:
        text += " with " + ", ".join(dict.fromkeys(c.replace("_", " ") for c in claims))
    text += ". Every figure comes from code"
    text += "; the model only added one polite sentence." if note.get("model") else "."
    return text


def reply_caption(sid, note, prev):
    status = (note.get("status") or "open").replace("_", " ")
    b = note.get("belief") or {}
    text = f"{sid} answers: {status} at {eur(note.get('theirs'))}"
    if prev and prev.get("theirs") and note.get("theirs"):
        drop = prev["theirs"] - note["theirs"]
        if drop > 0.005:
            text += f" (down {eur(drop)})"
    text += ". "
    if b.get("floor_p10") is not None:
        text += (
            f"The belief keeps the hypotheses that predicted this reply: floor between {pct(b['floor_p10'])} "
            f"and {pct(b['floor_p90'])} of its quote, patience {b.get('patience', '–')}."
        )
    return text


def plan_caption(note):
    table = note.get("table") or []
    best = min(table, key=lambda r: r[1]) if table else None
    n = len(table)
    text = f"The planner plays {n} candidate price{'s' if n != 1 else ''} forward against 400 hypotheses "
    text += "and scores each in euros. "
    if note.get("what") == "planner_accept":
        return text + f"Accepting its sheet now ({eur(note.get('accept'), 0)}) beats every further step."
    text += f"{eur(note.get('price'))} has the lowest expected total cost ({eur(note.get('expected'), 0)})"
    if best and len(best) > 2 and best[2] is not None:
        text += f", walk risk {pct(best[2])}"
    return text + "."


def guard_caption(note):
    what, sid = note.get("what"), note.get("supplier")
    fields = {k: v for k, v in note.items() if k not in ("event", "at", "t", "what", "supplier", "transfers")}
    if what == "rate_learned":
        fields = {"was": pct(fields.get("was")), "now": pct(fields.get("now"))}
    detail = ", ".join(f"{k} {v}" for k, v in list(fields.items())[:3] if not isinstance(v, (dict, list)))
    return f"{GUARDS.get(what, what)}{' · ' + sid if sid else ''}." + (f" ({detail})" if detail else "")


def story(events, source="recorded", choices=None):
    head, result = head_of(events), result_of(events)
    suppliers = head.get("suppliers") or sorted({e["supplier"] for e in events if e.get("supplier")})
    main = main_supplier(events, suppliers)
    cards = {s: supplier_card(events, s) for s in suppliers}
    steps = [intro_step(head, suppliers, result)]
    steps += [prep_step(cards[s]) for s in suppliers if cards[s]["q0"]]
    last_in, sends, seen_guards, last_reply = {}, {}, set(), {}
    for e in events:
        kind, sid = e.get("event"), e.get("supplier")
        if kind == "message_in":
            last_in[sid] = e
            continue
        if kind == "message_out" and e.get("action"):
            action = e["action"] or {}
            accept = action.get("type") == "accept"
            caption = (
                f"Close: accept {sid}'s sheet {action.get('term_sheet_id') or ''} - the cheapest standing sheet "
                "that keeps every must-have and beats the alternative."
                if accept
                else "Close: walk away - no standing sheet beats the alternative."
            )
            steps.append(
                step(
                    "close",
                    "Accept" if accept else "Walk away",
                    caption,
                    [("policy", "runner"), ("runner", "arena")],
                    supplier=sid,
                    action=action,
                )
            )
            continue
        if kind != "note":
            continue
        what = e.get("what")
        if what == "send" and not e.get("mirror"):
            n = sends[sid] = sends.get(sid, 0) + 1
            if sid != main and n > 1:
                continue
            msg = next(
                (x for x in events[events.index(e) :] if x.get("event") == "message_out" and x.get("supplier") == sid),
                None,
            )
            steps.append(
                step(
                    "send",
                    f"Round {n} · our offer to {sid}",
                    send_caption(sid, e, msg, n == 1),
                    [("policy", "writer"), ("writer", "runner"), ("runner", "arena")],
                    supplier=sid,
                    rnd=n,
                    note=e,
                    message=_message(msg),
                )
            )
        elif what == "reply" and sends.get(sid):
            show = sid == main or e.get("status") in DONE
            prev, last_reply[sid] = last_reply.get(sid), e
            if not show:
                continue
            steps.append(
                step(
                    "reply",
                    f"Round {sends[sid]} · {sid} answers",
                    reply_caption(sid, e, prev),
                    [("arena", "runner"), ("runner", "policy"), ("policy", "belief")],
                    supplier=sid,
                    rnd=sends[sid],
                    note=e,
                    message=_message(last_in.get(sid)),
                )
            )
        elif what in ("planner", "planner_accept") and sid == main:
            steps.append(
                step(
                    "plan",
                    f"Round {sends.get(sid, 0) + 1} · the planner chooses",
                    plan_caption(e),
                    [("belief", "planner"), ("economics", "planner"), ("planner", "policy")],
                    supplier=sid,
                    rnd=sends.get(sid, 0) + 1,
                    note=e,
                )
            )
        elif what in GUARDS and what != "planner_accept":
            key = (what, sid)
            if key in seen_guards and what not in ("dominated", "mirror", "last_sheet"):
                continue
            seen_guards.add(key)
            steps.append(step("guard", GUARDS[what], guard_caption(e), [("economics", "policy")], supplier=sid, note=e))
    if result:
        steps.append(result_step(result, head))
    steps.append(
        step(
            "outro",
            "How HackIt Buyer works",
            "Code owns every number. A belief about each supplier, narrowed by every reply, and a planner that "
            "scores candidate prices in euros - not a fixed recipe. Guards that never bend: no sheet above the "
            "alternative, no token moves, never push after a final offer, no walk-away while an acceptable sheet "
            "stands.",
            [("runner", "records"), ("runner", "memory")],
        )
    )
    q0s = {s: cards[s]["q0"] for s in suppliers}
    return {
        "source": source,
        "session": head.get("session"),
        "case_id": head.get("case_id"),
        "title": head.get("title"),
        "type": head.get("type"),
        "goal": head.get("goal"),
        "set": head.get("set"),
        "main": main,
        "result": result,
        "suppliers": list(cards.values()),
        "rounds": {s: rounds(events, s, q0s[s]) for s in suppliers if q0s[s]},
        "choices": choices or [],
        "steps": steps,
    }


def result_step(result, head):
    c_open, c_deal, alt = result.get("c_open"), result.get("c_deal"), result.get("c_alt") or head.get("c_alt")
    if result.get("deal") and c_open and c_deal:
        saved = c_open - c_deal
        caption = (
            f"Deal with {result.get('supplier_id')}: total cost {eur(c_deal, 0)} against {eur(c_open, 0)} for its "
            f"opening quote - {eur(saved, 0)} saved ({pct(saved / c_open)})."
        )
    elif result.get("deal"):
        caption = f"Deal with {result.get('supplier_id')} at total cost {eur(c_deal, 0)}."
    else:
        caption = f"No deal: the alternative ({eur(alt, 0)}) was cheaper than every sheet on the table."
    score = result.get("score")
    if score is not None and not (isinstance(score, float) and math.isnan(score)):
        caption += f" Arena score {score:.3f}."
    return step("result", "Result", caption, [("runner", "records"), ("runner", "memory")], **result)


def _message(m):
    if not m:
        return None
    return {
        k: (clip(v) if k == "body" else v)
        for k, v in m.items()
        if k in ("subject", "body", "status", "term_sheets", "refs", "claim_results", "from", "attachments")
    }


# -- picking the run ------------------------------------------------------------------------------------
def runs(live_runs):
    """[(choice, events)]: bundled runs first, then recorded live runs that reached an end."""
    out = []
    for name, events in bundled().items():
        out.append((_choice(events, "bundled", name), events))
    for events in live_runs:
        session = str(head_of(events).get("session") or "")
        if HIDE_SIM and session.startswith("sim"):
            continue
        if any(e.get("event") == "end" for e in events) and session:
            out.append((_choice(events, "recorded"), events))
    return out


def _choice(events, source, name=None):
    head, result = head_of(events), result_of(events)
    return {
        "session": head.get("session"),
        "name": name,
        "case_id": head.get("case_id"),
        "title": head.get("title"),
        "set": head.get("set"),
        "score": result.get("score"),
        "deal": result.get("deal"),
        "source": source,
    }


def pick(all_runs, session=None):
    """The run to show: the asked session; else the bundled default; else the best recorded deal."""
    if session:
        for choice, events in all_runs:
            if session in (choice["session"], choice["name"]):
                return choice, events
    for choice, events in all_runs:
        if choice["name"] == DEFAULT:
            return choice, events
    deals = [r for r in all_runs if r[0]["deal"]] or all_runs
    if not deals:
        return None, []
    return max(deals, key=lambda r: (r[0]["source"] == "bundled", r[0]["score"] or 0))


def storyboard(live_runs, session=None):
    all_runs = runs(live_runs)
    choice, events = pick(all_runs, session)
    if choice is None:
        return {"found": False, "steps": [], "choices": []}
    seen, choices = set(), []
    for c, _ in all_runs:
        if c["session"] not in seen:
            seen.add(c["session"])
            choices.append(c)
    return {"found": True, **story(events, choice["source"], choices)}
