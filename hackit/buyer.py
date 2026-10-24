"""The Hack It buyer: the agent the kit plays (`decide(case, threads)`), plus `prepare(case)`.

One case, three phases:

  prepare   before the session opens (no 60 s clock yet): dossier and evidence per supplier, and
            the four departments study each supplier in parallel
  decide    every move: read the threads, update what we believe about the supplier's floor, let the
            committee choose package, evidence and words, let code set the price and write the e-mail
  close     accept the cheapest acceptable standing sheet, or walk away when nothing beats our
            alternative

Code owns every number. The price follows a "compass": where we believe the supplier will end up.
Before it answers, that belief comes from the data (its full cost, or the room its kind of supplier
usually gives). Once it answers, its own concessions tell us: suppliers concede in shrinking steps,
so the steps so far predict where the series stops. We never offer above that point, above our
alternative, or above its own counter.

The state is rebuilt from the threads at every move, so a crashed case resumes where it was.
"""

import json
import logging
import math
import re
import time
from pathlib import Path

from arena_kit.cost import complete_offer, must_haves_ok, total_cost
from arena_kit.docs import quote_terms, standard_terms
from arena_kit.play import latest, live_threads, moves_left, standing

from . import committee, dossier, evidence, menu
from .minutes import Minutes

log = logging.getLogger("hackit")

SHARE = {"hold": 0.05, "small": 0.2, "normal": 0.33, "large": 0.5}  # of the gap, per round
MIN_STEP = 0.003  # every new sheet moves at least this share of the price: no move, no concession
PATIENCE = 0.32  # cumulative relative gap we show before we speed up (suppliers walk near 0.4)
OPEN_FLOOR = 0.80  # we never open below this share of its quote: an insult costs patience
OPEN_CUSHION = 0.03  # we open this far below where we expect it to end
SHOULD_COST_BIAS = 0.97  # our should-cost runs high: practice deals revealed full costs a few % below it
# room a supplier usually gives below its quote, by category goal (before it says anything)
ROOM = {"leverage": 0.14, "routine": 0.10, "quality_first": 0.10, "secure_supply": 0.07, "stabilise": 0.05}
ROOM_BY_NEED = {
    "price_frozen": -0.08,
    "idle_capacity": 0.03,
    "full_capacity": -0.03,
    "deprioritising_us": -0.02,
    "values_us": 0.01,
    "stale_costing": 0.01,
    "approval_ladder": -0.01,
}
LEAK = re.compile(
    r"budget|deadline|urgent|no alternative|target price|walk away|last chance|\d|\b[a-z]+:[\w.@#-]+", re.I
)
DATA_ID = re.compile(r"\b(?:idx|quote|offer|sc|qn|po|cost|fc|news|doc|ctr):[\w.:@#/-]*\w")
TEMPLATES = {
    "warm": "Thank you for your offer and for working with us. We value the cooperation and would like to "
    "find terms that work for both sides.",
    "neutral": "Thank you for your offer. We have reviewed it carefully against our data.",
    "firm": "Thank you for your offer. In its current form we cannot accept it.",
}


def safe_std(events):
    """The supplier's standard terms from its quote PDF; an unreadable line is skipped, not fatal."""
    try:
        return standard_terms(events)
    except Exception:  # noqa: BLE001
        pass
    for msg in reversed(events):
        for att in msg.get("attachments") or []:
            if att.get("name", "").startswith("quote") and att.get("text"):
                good = []
                for line in att["text"].splitlines():
                    try:
                        quote_terms(f"Our standard terms\n{line}")
                        good.append(line)
                    except Exception:  # noqa: BLE001
                        continue
                return quote_terms("\n".join(good)).get("standard", {})
    return {}


def supplier_msgs(events):
    return [m for m in events if m.get("from") != "buyer"]


class Seat:
    """Our seat opposite one supplier: what we know about it and what we planned."""

    def __init__(self, sid):
        self.sid = sid
        self.dossier = {}
        self.study = {}
        self.evidence = []
        self.est = {}
        self.std = {}
        self.main = None  # our main non-price package (complete terms, price is a placeholder)
        self.quote_eq = None  # its opening price, at our main terms
        self.prior = None  # where we expect it to end, before it answers
        self.full_floor = None  # never below this: its full cost plus a minimum margin, at our terms
        self.ceiling = None  # never above this: our alternative's break-even
        self.rel_opt = None  # strategic goals: the price with the lowest C for us
        self.ready = False


class CommitteeBuyer:
    def __init__(self, package=None, dossiers=None, use_model=True, log_dir="logs/hackit"):
        self.pkg = package
        self.cache = dossiers or {}
        self.use_model = use_model
        self.seats = {}
        self.case_id = None
        self.minutes = None
        self.log_dir = log_dir
        self.arrived = {}  # (sid, n supplier messages) -> when we first saw it
        self.c_open = None  # C of the cheapest opening quote: no deal may cost more

    # ---------- phase A: before the session opens ----------
    def prepare(self, case, budget_s=20.0):
        self._reset(case)
        t0 = time.monotonic()
        for sid in case["suppliers"]:
            seat = self.seats.setdefault(sid, Seat(sid))
            seat.dossier = self.cache.get(sid) or dossier.build(self.pkg, sid, case.get("parts") or [])
            seat.evidence = evidence.locker(self.pkg, case, sid)
        if self.use_model:
            found = committee.study({sid: self._brief(case, s) for sid, s in self.seats.items()}, budget_s)
            for (sid, name), out in found.items():
                if out:
                    self.seats[sid].study[name] = out
        self.minutes.add(
            "prepare",
            seconds=round(time.monotonic() - t0, 1),
            seats={
                sid: {
                    "needs": list(s.dossier.get("needs", {})),
                    "evidence": [e["text"] for e in s.evidence],
                    "study": s.study,
                }
                for sid, s in self.seats.items()
            },
        )

    def _reset(self, case):
        if self.case_id != case["case_id"]:
            self.case_id = case["case_id"]
            self.seats = {}
            self.minutes = Minutes(case, self.log_dir)

    def _brief(self, case, seat):
        b = case["buyer"]
        keep = {
            k: b.get(k) for k in ("category_goal", "need_per_year", "horizon_years", "current_unit_price", "must_haves")
        }
        return (
            f"CASE {case.get('title')} ({case.get('type')})\n{case.get('briefing')}\nOUR SIDE {json.dumps(keep)}\n"
            f"NEGOTIABLE {case.get('negotiable')} OPTIONS {json.dumps(case.get('term_options'), default=str)}\n"
            f"DOSSIER\n{dossier.summary(seat.dossier)}"
        )

    # ---------- the seat, once its quote is in ----------
    def _setup(self, case, seat, events):
        seat.std = safe_std(events)
        _, quote = standing(events)
        live_needs = {}
        for m in supplier_msgs(events):
            for need, ev in dossier.needs_in(m.get("body", ""), "its e-mail").items():
                live_needs.setdefault(need, ev)
        for need, ev in live_needs.items():
            seat.dossier.setdefault("needs", {}).setdefault(need, ev)
        seat.est = menu.estimate(case, seat.std, seat.dossier, quote, self.pkg)
        q = quote.get("unit_price")
        seat.main = menu.base_terms(case, seat.std, q)
        seat.quote_eq = self._eq(case, seat, quote)
        seat.evidence = [e for e in evidence.locker(self.pkg, case, seat.sid, q)]
        # never below its full cost plus a minimum margin (what it is worth to it, at our terms)
        if seat.est.get("full_cost"):
            # its quote carries at least a healthy margin: a should-cost above that is our estimate's
            # error (the package names 5 to 15 %), so the quote bounds it
            healthy = (case["buyer"].get("sustainable_margin_pct") or 5) / 100
            quoted = menu.worth(case, quote, seat.est)
            if quoted:
                seat.est["full_cost"] = round(min(seat.est["full_cost"] * SHOULD_COST_BIAS, quoted * (1 - healthy)), 2)
            seat.full_floor = self._price_for_worth(case, seat, seat.est["full_cost"] * (1 + menu.MIN_MARGIN))
        seat.ceiling = menu.break_even(case, seat.main, seat.std, seat.est)
        seat.rel_opt = menu.relationship_optimum(
            case, seat.main, seat.std, seat.est, 0.6 * seat.quote_eq, seat.quote_eq
        )
        seat.prior = self._prior(case, seat)
        seat.ready = True
        self.minutes.add(
            "seat",
            supplier=seat.sid,
            quote=quote,
            standard=seat.std,
            main=seat.main,
            quote_at_our_terms=seat.quote_eq,
            prior=seat.prior,
            full_floor=seat.full_floor,
            ceiling=seat.ceiling,
            relationship_optimum=seat.rel_opt,
            estimate=seat.est,
            needs=list(seat.dossier.get("needs", {})),
        )

    def _prior(self, case, seat):
        """Where we expect the supplier to end before it says anything, at our main terms."""
        goal = case["buyer"].get("category_goal")
        needs = seat.dossier.get("needs", {})
        room = ROOM.get(goal, 0.1) + sum(v for k, v in ROOM_BY_NEED.items() if k in needs)
        guess = seat.quote_eq * (1 - max(0.02, room))
        cur = case["buyer"].get("current_unit_price")
        if case.get("type") == "price_increase" and cur:
            moves = evidence.index_moves(self.pkg, (case.get("parts") or [None])[0]) if self.pkg else []
            fair = evidence.justified_change(moves)
            guess = min(guess, cur * (1 + max(0.0, fair or 0.0) / 200))
        return round(guess, 2)

    def _eq(self, case, seat, terms):
        """A package's price at our main terms: the price that would cost us the same."""
        c = total_cost(case, complete_offer(case, {**seat.main, **terms}) if terms else seat.main, seat.std)
        p = menu.price_for_cost(case, seat.main, c, seat.std) if math.isfinite(c) else None
        return round(p, 2) if p else None

    def _price_for_worth(self, case, seat, value):
        a, b = 1.0, 2.0
        wa, wb = (
            menu.worth(case, menu.at_price(case, seat.main, a), seat.est),
            menu.worth(case, menu.at_price(case, seat.main, b), seat.est),
        )
        if wa is None or wb is None or wa == wb:
            return None
        return a + (value - wa) * (b - a) / (wb - wa)

    # ---------- reading the threads ----------
    def _paths(self, case, seat, events):
        """(ours, theirs): our main-sheet prices and its standing prices, both at our main terms."""
        ours, theirs = [], []
        for m in events:
            sheets = m.get("term_sheets") or []
            if not sheets:
                continue
            if m.get("from") == "buyer":
                ours.append(self._eq(case, seat, sheets[0]["terms"]))
            else:
                theirs.append(self._eq(case, seat, sheets[0]["terms"]))
        return [p for p in ours if p], [p for p in theirs if p]

    @staticmethod
    def _response(ours, theirs):
        """Its concession per euro we moved, round by round. theirs[0] is its quote, theirs[k] its
        answer to ours[k-1]; our move k (ours[k] - ours[k-1]) is answered by theirs[k + 1]."""
        out = []
        for k in range(1, len(ours)):
            if k + 1 < len(theirs) and ours[k] > ours[k - 1]:
                out.append(round((theirs[k] - theirs[k + 1]) / (ours[k] - ours[k - 1]), 2))
        return out

    def _opening_cost(self, case, threads):
        """C of the cheapest opening quote that keeps the must-haves (the score's zero point)."""
        costs = []
        for events in threads.values():
            first = next((m for m in events if m.get("from") != "buyer" and m.get("term_sheets")), None)
            if first is None:
                continue
            terms, std = first["term_sheets"][0]["terms"], safe_std(events)
            if terms.get("unit_price") is not None and must_haves_ok(case, terms, std):
                costs.append(total_cost(case, terms, std))
        return min(costs) if costs else None

    def _firm(self, ours, theirs):
        """It barely answers our moves any more (twice under a quarter of our step): its floor."""
        rho = self._response(ours, theirs)
        return len(rho) >= 2 and rho[-1] < 0.25 and rho[-2] < 0.35

    def _compass(self, case, seat, ours, theirs):
        """How far up we are willing to go now, at our main terms. Before it shows its hand: the data's
        estimate, raised to its estimated cost floor (never squeeze below it). Once it is firm: its
        counter. Never above its own counter."""
        belief = theirs[-1] if theirs and self._firm(ours, theirs) else seat.prior
        for bound in (seat.full_floor, seat.rel_opt):
            if bound:
                belief = max(belief, bound)
        if theirs:
            belief = min(belief, theirs[-1])
        return round(belief, 2)

    def _take_now(self, seat, ours, theirs):
        """Accept its standing sheet without waiting for a final offer? Only on evidence from its own
        behaviour (it is firm), or when it came down to what the data told us to expect - and never
        on its first answer. Our cost estimates cap our offers but never trigger a deal."""
        if len(theirs) < 3:
            return None
        if self._firm(ours, theirs):
            return f"it is firm: concession per euro we moved {self._response(ours, theirs)[-2:]}"
        if theirs[-1] <= seat.prior * 1.002:
            return f"its counter {theirs[-1]} reached what the data led us to expect ({seat.prior})"
        return None

    def _used(self, threads):
        refs = set()
        for events in threads.values():
            for m in events:
                if m.get("from") == "buyer":
                    refs |= set(DATA_ID.findall(m.get("body", "")))
        return refs

    def _acceptable(self, case, seat, events):
        """C of its standing sheet if we may accept it (must-haves kept, beats our alternative)."""
        oid, terms = standing(events)
        if not oid or terms.get("unit_price") is None:
            return None
        std = safe_std(events) or seat.std
        if not must_haves_ok(case, terms, std):
            return None
        c = menu.cost(case, terms, std, seat.est)
        # never a deal dearer than the cheapest opening quote: that scores 0 (P02 once accepted a
        # cheaper price whose index clause made it dearer than the opening)
        if self.c_open is not None and total_cost(case, terms, std) >= self.c_open:
            return None
        alt = case["buyer"].get("alternative") or {}
        if alt.get("terms", {}).get("unit_price") and alt.get("kind") not in (None, "none"):
            if c >= total_cost(case, alt["terms"]):
                return None
        return c

    def _best(self, case, threads):
        best = None
        for sid in live_threads(threads):
            seat = self.seats.get(sid)
            if seat is None or not seat.ready:
                continue
            c = self._acceptable(case, seat, threads[sid])
            if c is not None and (best is None or c < best[0]):
                best = (c, sid, standing(threads[sid])[0])
        return best

    def _accept(self, best, why):
        self.minutes.add("accept", supplier=best[1], offer=best[2], cost=round(best[0]), why=why)
        return {"supplier_id": best[1], "message": {"action": {"type": "accept", "term_sheet_id": best[2]}}}

    def _walk(self, threads, why):
        self.minutes.add("walk_away", why=why)
        live = live_threads(threads) or list(threads)
        return {"supplier_id": live[0], "message": {"action": {"type": "walk_away"}}}

    def _waiting(self, threads):
        """The live thread whose supplier message has waited longest for our answer."""
        now = time.monotonic()
        order = []
        for sid in live_threads(threads):
            n = len(supplier_msgs(threads[sid]))
            seen = self.arrived.setdefault((sid, n), now)
            if threads[sid][-1].get("from") != "buyer":
                order.append((seen, sid))
        return [sid for _, sid in sorted(order)]

    # ---------- every move ----------
    def decide(self, case, threads):
        t0 = time.monotonic()
        self._reset(case)
        for sid in threads:
            seat = self.seats.setdefault(sid, Seat(sid))
            if not seat.dossier:
                seat.dossier = self.cache.get(sid) or dossier.build(self.pkg, sid, case.get("parts") or [])
            if not seat.ready and standing(threads[sid])[0]:
                try:
                    self._setup(case, seat, threads[sid])
                except Exception:  # noqa: BLE001 - a supplier we cannot price is only closed on
                    log.exception("setup of %s failed", sid)
        live = [s for s in live_threads(threads) if self.seats[s].ready]
        if self.c_open is None:
            self.c_open = self._opening_cost(case, threads)
        if not live:
            return self._walk(threads, "no supplier with a usable quote")
        best = self._best(case, threads)
        # a supplier that agreed or made its final offer, and nobody else can still beat it
        done = [s for s in live if latest(threads[s]).get("status") in ("agreed", "final_offer")]
        open_ = [s for s in live if s not in done and moves_left(case, threads[s]) > 0]
        if best and best[1] in done and not self._others_can_beat(case, threads, best, open_):
            return self._accept(best, f"{best[1]} is {latest(threads[best[1]]).get('status')}; no one else can beat it")
        if not open_:
            return (
                self._accept(best, "no thread left to negotiate")
                if best
                else self._walk(threads, "nothing beats our alternative")
            )
        waiting = [s for s in self._waiting(threads) if s in open_] or open_
        sid = waiting[0]
        seat, events = self.seats[sid], threads[sid]
        self._listen(case, seat, events)
        ours, theirs = self._paths(case, seat, events)
        compass = self._compass(case, seat, ours, theirs)
        # its counter is already where we believe it ends: take it - never on its first answer, a
        # first concession says nothing yet about where it stops
        if best and best[1] == sid and (why := self._take_now(seat, ours, theirs)):
            return self._accept(best, why)
        if moves_left(case, events) <= 1 and best and best[1] == sid:
            return self._accept(best, "last message in this thread")
        # how much time the committee has: other threads wait while we think
        others = len(live_threads(threads)) - 1
        budget = 22.0 if others == 0 else (12.0 if others == 1 else 0.0)
        situation = self._situation(case, threads, sid, seat, ours, theirs, compass)
        items = situation.pop("_items")
        notes, decision = {}, None
        if self.use_model and budget > 0:
            notes, decision = committee.meet(situation["text"], budget, departments=budget >= 15)
        move = self._move(case, threads, sid, seat, ours, theirs, compass, items, situation["evidence"], decision)
        self.minutes.add(
            "move",
            supplier=sid,
            seconds=round(time.monotonic() - t0, 1),
            ours=ours,
            theirs=theirs,
            response=self._response(ours, theirs),
            compass=compass,
            departments=notes,
            director=decision,
            sent=move["message"],
        )
        return move

    def _listen(self, case, seat, events):
        """Its own e-mails say what it needs, also for a supplier our data does not know: a need heard
        live joins the dossier, and a cash need raises what money is worth to it."""
        heard = {}
        for m in supplier_msgs(events):
            for need, ev in dossier.needs_in(m.get("body", ""), "its e-mail").items():
                heard.setdefault(need, ev)
        new = [n for n in heard if n not in seat.dossier.setdefault("needs", {})]
        for need in new:
            seat.dossier["needs"][need] = heard[need]
        if "cash" in new:
            seat.est["interest_rate"] = round(seat.est.get("interest_rate", 0.07) + menu.CASH_PREMIUM, 3)
        if new:
            self.minutes.add("heard", supplier=seat.sid, needs=new)

    def _trade_for_needs(self, seat, items, chosen):
        """A menu trade that answers a need we know of, when the committee offered none."""
        wants = seat.dossier.get("needs", {})
        fields = []
        if "cash" in wants or "late_payment_grievance" in wants:
            fields.append("payment_days")
        if {"idle_capacity", "deprioritising_us", "values_us"} & set(wants):
            fields += ["contract_months", "volume_per_year"]
        if "full_capacity" in wants or "supply_risk" in wants:
            fields += ["lead_time_days", "contract_months"]
        if "hedged" not in wants:
            fields.append("index_clause")
        for f in fields:
            for i in items[1:]:
                if i["id"] not in chosen and i["label"].startswith(f + "="):
                    return i["id"]
        return None

    def _others_can_beat(self, case, threads, best, open_):
        """Can a bidder still negotiating get below the best acceptable sheet? Its compass says."""
        for sid in open_:
            seat = self.seats[sid]
            ours, theirs = self._paths(case, seat, threads[sid])
            target = self._compass(case, seat, ours, theirs)
            if total_cost(case, menu.at_price(case, seat.main, target), seat.std) < best[0] - 1:
                return True
        return False

    def _situation(self, case, threads, sid, seat, ours, theirs, compass):
        """What the committee reads: the case, our study, the thread, the menu and the evidence."""
        price = self._next_price(case, seat, ours, theirs, compass, "normal")
        items = menu.build(case, seat.std, seat.est, menu.at_price(case, seat.main, price))
        used = self._used(threads)
        ev = [e for e in seat.evidence if not set(e["refs"]) & used]
        if len(threads) > 1 and (r := evidence.rival(case, threads, sid, standing)):
            ev.insert(0, r)
        thread_txt = []
        for m in threads[sid][-6:]:
            who = "US" if m.get("from") == "buyer" else f"SUPPLIER ({m.get('from')})"
            sheets = [s.get("terms") for s in m.get("term_sheets") or []]
            extra = {k: m.get(k) for k in ("status", "claim_results", "signals", "missing_fields") if m.get(k)}
            thread_txt.append(
                f"{who} {json.dumps(extra, default=str)} sheets {json.dumps(sheets, default=str)}\n"
                f"{(m.get('body') or '')[:900]}"
            )
        study = "\n".join(
            f"{k.upper()} STUDY: {json.dumps({x: v[x] for x in v if x != '_seconds'})}" for k, v in seat.study.items()
        )
        text = (
            f"{self._brief(case, seat)}\n{study}\n\nTHE THREAD WITH {sid} (latest last):\n"
            + "\n".join(thread_txt)
            + f"\n\nPRICES AT OUR MAIN TERMS: ours {ours}, its {theirs}. Messages left: {moves_left(case, threads[sid])}."
            + f"\nOTHER BIDDERS: {[s for s in threads if s != sid] or 'none'}"
            + f"\n\nMENU (this round's packages; code sets the final price by your concession):\n{menu.text(case, items)}"
            + "\n\nEVIDENCE (not used yet):\n"
            + "\n".join(f"{e['id']}: {e['text']}" for e in ev[:8])
        )
        return {"text": text, "evidence": ev, "_items": items}

    def _next_price(self, case, seat, ours, theirs, compass, concession):
        """Our next price at our main terms."""
        if not ours:  # the opening: below where we expect it to end, never an insult
            p = max(seat.quote_eq * OPEN_FLOOR, compass * (1 - OPEN_CUSHION))
            cur = case["buyer"].get("current_unit_price")
            if case.get("type") == "price_increase" and cur:
                p = min(p, cur)
        else:
            last, counter = ours[-1], theirs[-1] if theirs else seat.quote_eq
            gap = max(0.0, counter - last)
            shown = sum(max(0.0, t - o) / t for o, t in zip(ours, theirs[1:], strict=False) if t)
            share = SHARE.get(concession, 0.3)
            if shown > PATIENCE:
                share = max(share, 0.3)
            p = last + share * gap
            if "approval_ladder" in seat.dossier.get("needs", {}):
                p = min(p, last * 1.015)  # its committee approves small steps only
            p = max(p, last * (1 + MIN_STEP))
            p = min(p, max(compass, last * (1 + MIN_STEP)), counter * 0.999)
        if seat.ceiling:
            p = min(p, seat.ceiling)
        return round(max(p, 0.01), 2)

    def _move(self, case, threads, sid, seat, ours, theirs, compass, items, ev, decision):
        d = decision or {}
        concession = d.get("concession") if d.get("concession") in SHARE else "normal"
        price = self._next_price(case, seat, ours, theirs, compass, concession)
        if ours and price <= ours[-1] and theirs and theirs[-1] > ours[-1] * 1.002:
            price = round(ours[-1] * (1 + MIN_STEP), 2)
        by_id = {i["id"]: i for i in items}
        # the menu was priced around the "normal" step: move every package by the same amount
        shift = price - items[0]["terms"]["unit_price"]
        chosen = [d.get("main") if d.get("main") in by_id else "M0"]
        if not ours and not d:  # no committee: open with our main package and the best cash trade
            chosen += [i["id"] for i in items[1:2]]
        chosen += [x for x in d.get("alternatives", []) if x in by_id and x not in chosen][:2]
        if len(chosen) == 1 and (trade := self._trade_for_needs(seat, items, chosen)):
            chosen.append(trade)
        sheets = []
        for x in chosen[:3]:
            t = menu.at_price(case, by_id[x]["terms"], by_id[x]["terms"]["unit_price"] + shift)
            if must_haves_ok(case, t, seat.std) and t not in sheets:
                sheets.append(t)
        if not sheets:
            sheets = [menu.at_price(case, seat.main, price)]
        # evidence: the committee's picks, then the strongest; three in the first e-mail, else up to two
        by_ev = {e["id"]: e for e in ev}
        picks = [x for x in d.get("evidence", []) if x in by_ev]
        n = 3 if not ours else 2
        for e in ev:
            if len(picks) >= n:
                break
            if e["id"] not in picks and (not ours or e["kind"] == "rival"):
                picks.append(e["id"])
        picks = picks[:n]
        body = self._email(case, seat, d, [by_ev[x] for x in picks], sheets)
        subject = f"Re: {case.get('title')}"[:300]
        return {
            "supplier_id": sid,
            "message": {"subject": subject, "body": body, "term_sheets": [{"terms": t} for t in sheets]},
        }

    def _email(self, case, seat, d, claims, sheets):
        para = " ".join(str(d.get("email_paragraph") or "").split())
        para = re.sub(r"^(dear|hello|hi)\b[^,]{0,60},\s*", "", para, flags=re.I)
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", para) if s and not LEAK.search(s)]
        para = " ".join(sentences) or TEMPLATES.get(d.get("tone"), TEMPLATES["neutral"])
        question = str(d.get("question") or "")
        if LEAK.search(question):
            question = ""
        lines = ["Dear Sir or Madam,", "", para]
        if claims:
            lines += ["", "For reference:"] + [f"- {c['text']}" for c in claims]
        lines += ["", "Our proposal:" if len(sheets) == 1 else "We can offer either of these packages:"]
        for k, t in enumerate(sheets):
            head = "" if len(sheets) == 1 else f"Option {chr(65 + k)}: "
            lines.append(f"- {head}{self._words(case, t)}")
        if question:
            lines += ["", question]
        lines += ["", "Kind regards", "Purchasing, Hollerau Pumpenwerke GmbH"]
        return "\n".join(lines)[:7900]

    def _words(self, case, t):
        out = [f"unit price EUR {t['unit_price']:.2f}"]
        names = {
            "payment_days": "payment {} days net",
            "contract_months": "{} months contract",
            "volume_per_year": "{} pieces a year",
            "lead_time_days": "lead time {} days",
            "effective_date": "new price from {}",
            "incoterm": "delivery {}",
            "tooling_eur": "tooling EUR {}",
            "ppm_commitment": "{} ppm quality commitment",
            "chargeback_eur_per_complaint": "EUR {} per complaint",
        }
        for f in case["negotiable"]:
            if f == "unit_price":
                continue
            v = t.get(f)
            if f == "index_clause":
                out.append(
                    "no index clause"
                    if not v
                    else f"{v['series']} index clause ({int(v['material_share'] * 100)}"
                    f"% material, deadband {v['deadband_pct']}%)"
                )
            elif f in names and v is not None:
                out.append(names[f].format(v))
        return ", ".join(out)


def load_dossiers(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
