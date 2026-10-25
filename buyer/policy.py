"""The negotiation, per supplier thread: plan the non-price terms by C at the supplier's own
valuation, open at a proven anchor, then let the planner choose each next price against the
belief about this supplier. Close on its final offer, its agreement or the round limit.

Guards that never bend (each one paid for by a practice loss somewhere [pc]):
- code computes every number; a sheet always carries every negotiable field with allowed values;
- no sheet above the price where C meets the alternative; no deal that breaks a must-have;
- after `final_offer` or `agreed`: accept, mirror a cheaper sheet it offered itself, or - only if
  its standing sheet is unacceptable to us - one last sheet at our ceiling;
- never step backwards, never resend an unchanged sheet, never make a token move;
- a bad deal beats no deal: no path walks away while an acceptable sheet stands.
"""

import base64
import logging
import math
import re
import time

from arena_kit.docs import quote_terms, standard_terms
from arena_kit.play import ENDED, latest, moves_left, standing

from . import config, llm, memory, planner, reader, supplier, telemetry, writer
from . import economics as eco
from .belief import AGREED, FINAL, OPEN, Belief
from .datapkg import CaseData
from .evidence import EvidenceBank, months_back, rival_offer
from .thread import Thread

log = logging.getLogger("buyer")
DONE = {"agreed", "final_offer"}
REFUSABLE = {"payment_days", "lead_time_days", "contract_months", "volume_per_year", "incoterm", "effective_date"}

# The bots write their asks in a few fixed sentences. [pc]
ASKS = {
    "volume": r"volume (can )?you (can )?commit|binding (higher )?volume|higher (order )?volume|volume figures"
    r"|confirm .{0,40}volume|anticipated (order )?volume",
    "proof": r"proof of|send us the data|figures is that based|provide the (volume|forecast|binding)"
    r"|forecast data|volume data",
    "payment": r"faster payment|pay(ment)? (terms )?(earlier|faster|sooner|within)|need .{0,12}days payment"
    r"|shorter payment|quicker payment",
}


def asks_in(body):
    return [k for k, pat in ASKS.items() if re.search(pat, body or "", re.I)]


def std_terms(events):
    """Its standard terms from the latest quote PDF; a line the kit cannot parse is dropped
    instead of the whole quote (a hidden case's odd line once lost a supplier at the close). [pc]"""
    try:
        return standard_terms(events)
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    for msg in reversed(events):
        for att in msg.get("attachments") or []:
            if att.get("name", "").startswith("quote") and att.get("text"):
                good = []
                for line in att["text"].splitlines():
                    try:
                        quote_terms(f"Our standard terms\n{line}")
                        good.append(line)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        continue
                return quote_terms("\n".join(good)).get("standard", {})
    return {}


def message_key(events):
    last = events[-1] if events else {}
    return f"{len(events)}:{last.get('message_id')}:{last.get('status')}"


def priced_sheets(message):
    """Its standing sheet first, then any priced alternatives it offered."""
    out = [sh for sh in message.get("term_sheets") or [] if (sh.get("terms") or {}).get("unit_price") is not None]
    for alt in message.get("alternatives") or []:
        terms = alt.get("terms", alt) if isinstance(alt, dict) else None
        if terms and terms.get("unit_price") is not None:
            out.append({"term_sheet_id": alt.get("term_sheet_id") or alt.get("offer_id"), "terms": terms})
    return out


def safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one supplier's odd data must never cost the move
        log.exception("skipped: %s", getattr(fn, "__name__", fn))
        return default


class Negotiator:
    def __init__(self, case, pkg=None):
        self.case, self.data = case, CaseData(case, pkg)
        self.bank = EvidenceBank(self.data, case)
        self.threads = {}
        self.used = set()  # claim keys cited in this case (a rival's quote or offer: per supplier)
        self.notes = []
        self.started = time.time()
        self.prefetched = {}
        self.reader_text = {}  # what the reader agent was given, for the watch UI

    # -- setup -------------------------------------------------------------------------------
    def prefetch(self, threads):
        """The reader agent starts on every supplier at once: our texts about it and its quote
        e-mail. `setup` waits for it at most READER_SETUP_S after the case start."""
        for sid, events in threads.items():
            texts = safe(lambda sid=sid: supplier.supplier_texts(self.data, sid), []) or []
            first = latest(events).get("body") or ""
            text = "\n\n---\n\n".join([*(x for x in texts if x), f"ITS QUOTE E-MAIL:\n{first}"])
            self.prefetched[sid] = reader.submit("our data about the supplier and its quote e-mail", text)
            self.reader_text[sid] = text

    def absorb(self, t, found, where):
        """Labels the reader agent found, added to what the phrase lists found. Returns the new signals."""
        if reader.enabled():
            telemetry.emit(
                "llm",
                supplier=t.sid,
                role="reader",
                where=where,
                prompt=telemetry.clip(self.reader_text.get(t.sid)),
                answer=found,
                outcome="ok" if found else "none",
            )
        if not found:
            return []
        if where == "setup":  # an opening quote has no stance yet
            found = {**found, "stance": "unclear"}
        t.reader = found
        t.asks = sorted(set(t.asks) | set(found["asks"]))
        new = [s for s in found["signals"] if s not in t.prof["signals"]]
        if new:
            t.prof["signals"] = sorted(t.prof["signals"] + new)
            if "cash_need" in new:
                t.prof["rate"] = round(t.prof["rate"] + config.CASH_NEED_BONUS, 4)
                if t.est:
                    t.est["interest_rate"] = t.prof["rate"]
        self.note(
            t.sid,
            "reader",
            where=where,
            signals=found["signals"],
            asks=found["asks"],
            stance=found["stance"],
            evidence=found["evidence"],
            summary=found["summary"],
        )
        return new

    def setup(self, sid, events):
        case, t = self.case, Thread(sid)
        self.threads[sid] = t
        sheet_id, quote = standing(events)
        # no readable quote PDF: what it quoted is the best guess for what it leaves out
        t.std = safe(lambda: std_terms(events), {}) or {k: v for k, v in quote.items() if k != "unit_price"}
        t.prof = safe(lambda: supplier.profile(self.data, sid, latest(events).get("body")), None) or {
            "signals": [],
            "rate": config.DEFAULT_RATE,
            "warm": True,
        }
        wait = config.READER_SETUP_S - (time.time() - self.started)
        safe(lambda: self.absorb(t, reader.result(self.prefetched.get(sid), wait), "setup"))
        if not sheet_id or quote.get("unit_price") is None:
            t.state = "ended"
            return t
        q_price = float(quote["unit_price"])
        parts = case.get("parts") or []
        full_cost, own_plant = (None, False)
        if len(parts) == 1:  # a bundle's should-cost is not one part's cost
            full_cost, own_plant = safe(lambda: supplier.full_cost(self.data, parts[0], sid), (None, False))
        signals = set(t.prof["signals"])
        lead = quote.get("lead_time_days") or t.std.get("lead_time_days")
        overtime = config.OVERTIME_FULL if "capacity_full" in signals else config.OVERTIME_DEFAULT
        t.est = eco.estimate(
            case,
            t.std or quote,
            t.prof["rate"],
            full_cost,
            safe(lambda: supplier.real_ppm(self.data, sid)),
            lead,
            overtime,
            safe(self.index_moves, {}),
            "hedged" in signals,
        )
        goal = case["buyer"].get("category_goal")
        sustainable = case["buyer"].get("sustainable_margin_pct") or 0
        if goal in eco.RELATIONSHIP_GOALS and sustainable:
            quoted_value = eco.value(case, quote, t.est)
            # it quotes with its margin: our estimate never sits above the quote less the
            # sustainable margin; without a should-cost assume it quotes two points above it
            cap = quoted_value * (1 - sustainable / 100)
            t.full_cost_known = bool(full_cost)
            if full_cost:
                # the should-cost overshot the true full cost by 1-2 % on P01, P02 and P03 (the
                # relationship charge of 32 practice deals reveals it) [pc]
                full_cost *= config.SHOULD_COST_BIAS
            # D01 (no should-cost): its full cost sat at most at 48.9 against a 53.41 quote [pc]
            full_cost = min(full_cost, cap) if full_cost else quoted_value * (1 - (sustainable + 4) / 100)
            t.est["full_cost"] = full_cost
        t.near_cost = bool(own_plant and full_cost and full_cost >= config.NEAR_COST_RATIO * q_price)
        ratio = self.anchor_ratio(t, q_price)
        planned = safe(lambda: self.plan(t, quote, q_price * ratio))
        t.terms = planned[0] if planned else eco.complete_offer(case, eco.at_price(quote, q_price * ratio))
        t.anchor = t.terms["unit_price"]
        t.q0 = safe(lambda: eco.convert(case, quote, t.est, t.terms), q_price) or q_price
        if t.anchor >= t.q0 * config.MAX_SHARE_OF_COUNTER:
            t.anchor = round(t.q0 * ratio, 2)
            t.terms = eco.at_price(t.terms, t.anchor)
        if t.est.get("full_cost") and goal in eco.RELATIONSHIP_GOALS:
            t.target = safe(
                lambda: eco.relationship_optimum(case, t.terms, t.std, t.est, t.est["full_cost"] * 0.85, t.q0)
            )
            safe(lambda: self.clause_trade(t, quote, signals))
        t.theirs = [t.q0]
        mean, sd = supplier.floor_prior(case, t.prof, t.near_cost)
        mean, sd, learned = memory.adjust(case, mean, sd)
        if learned:
            self.note(sid, "memory", **learned, prior=(round(mean, 4), round(sd, 4)))
        if own_plant and full_cost:
            cost_share = eco.price_for_value(case, t.terms, t.est, full_cost) / t.q0
            mean = max(mean, min(0.995, cost_share - 0.01))
        t.belief = Belief(mean, sd, supplier.patience_prior(case, t.prof, t.near_cost))
        t.claims = safe(lambda: self.bank.for_supplier(sid, q_price, "hedged" in signals), []) or []
        self.note(
            sid,
            "setup",
            quote=quote,
            std=t.std,
            signals=t.prof.get("signals"),
            owner=t.prof.get("owner_type"),
            rate=t.est.get("interest_rate"),
            full_cost=t.est.get("full_cost"),
            terms=t.terms,
            q0=round(t.q0, 4),
            anchor=t.anchor,
            target=t.target,
            prior=(round(mean, 4), round(sd, 4)),
            claims=[c["kind"] for c in t.claims],
        )
        return t

    def anchor_ratio(self, t, q_price):
        case, signals = self.case, set(t.prof["signals"])
        ratio = config.ANCHOR.get(case["buyer"].get("category_goal"), config.ANCHOR_DEFAULT)
        if "price_frozen" in signals:
            ratio = max(ratio, config.ANCHOR_PRICE_FROZEN)
        if "capacity_full" in signals:
            ratio = max(ratio, config.ANCHOR_CAPACITY_FULL)
        if t.near_cost:
            ratio = max(ratio, config.ANCHOR_NEAR_COST)
        current = case["buyer"].get("current_unit_price")
        if case.get("type") == "price_increase" and current:
            ratio = min(ratio, current / q_price)  # a price increase opens at today's price at most
        return max(config.ANCHOR_MIN, ratio)

    def clause_trade(self, t, quote, signals):
        """Relationship goals: an index clause the supplier values well above what it costs us, given
        on top of a price at our relationship optimum. A lower fixed price only buys a relationship
        charge; the clause pays the supplier instead. Nordmark agreed at once to a 5 % copper deadband
        4.3 % below its fixed price (P02 0.735, our fixed-price runs 0.50 and 0.00). [pc]"""
        case = self.case
        if "index_clause" not in case["negotiable"] or t.terms.get("index_clause") or "hedged" in signals:
            return
        best = None
        for clause in (case.get("term_options") or {}).get("index_clause") or []:
            if not clause:
                continue
            gain = eco.clause_share(clause, t.est) - eco.clause_cost_share(case, clause)
            key = (round(gain, 4), clause.get("deadband_pct") or 0)
            if gain >= config.CLAUSE_MIN_GAIN and (best is None or key > best[0]):
                best = (key, clause)
        if not best:
            return
        terms = {**t.terms, "index_clause": best[1]}
        price = eco.price_for_value(case, terms, t.est, eco.value(case, t.terms, t.est))
        q0 = eco.convert(case, quote, t.est, terms)
        floor = eco.relationship_optimum(case, terms, t.std, t.est, t.est["full_cost"] * 0.85, max(q0, price) * 1.1)
        price = max(price, floor or 0.0)
        if not eco.must_haves_ok(case, eco.at_price(terms, price), t.std):
            return
        t.terms, t.anchor, t.target, t.q0 = eco.at_price(terms, price), round(price, 2), floor, max(q0, price * 1.01)
        self.note(t.sid, "clause_trade", clause=best[1], gain=best[0][0], anchor=t.anchor, target=floor)

    def index_moves(self):
        """Each clause series' change over the last twelve months: what the supplier expects."""
        out = {}
        for clause in (self.case.get("term_options") or {}).get("index_clause") or []:
            series = (clause or {}).get("series")
            if not series or series in out:
                continue
            ids = self.data.ids(f"idx:{series}@")
            if not ids:
                continue
            m2 = ids[-1].split("@")[-1]
            a, b = self.data.fact(f"idx:{series}@{months_back(m2, 12)}"), self.data.fact(ids[-1])
            if a and b and a.get("value") and b.get("value"):
                out[series] = b["value"] / a["value"] - 1
        return out

    def plan(self, t, ref_terms, ref_price):
        """Our planned terms. An index clause must beat the best fixed-price plan by a clear margin:
        a clause the supplier values like our cost only adds risk (P08, P18). [pc]"""
        case = self.case
        best = eco.plan_terms(case, t.std, t.est, ref_terms, ref_price)
        if not best or not best[0].get("index_clause"):
            return best
        clauses = (case.get("term_options") or {}).get("index_clause") or []
        if None not in clauses:
            return best
        fixed_case = {**case, "term_options": {**case["term_options"], "index_clause": [None]}}
        fixed = eco.plan_terms(fixed_case, t.std, t.est, ref_terms, ref_price)
        if fixed is None:
            return best
        spend = case["buyer"]["need_per_year"] * case["buyer"]["horizon_years"] * ref_price
        if best[1] <= fixed[1] - config.CLAUSE_MIN_GAIN * 0.75 * spend:
            return best
        return fixed

    # -- reading a reply ---------------------------------------------------------------------
    def read_reply(self, t, last, sheets, events, reader_s=0.0):
        case = self.case
        standing_terms = sheets[0]["terms"]
        status = last.get("status")
        reading = reader.submit("supplier e-mail in the negotiation", last.get("body")) if reader_s > 0 else None
        self.reader_text[t.sid] = last.get("body")
        t0 = time.monotonic()
        terms_before = dict(t.terms)
        self.learn_rate(t, sheets)
        self.adopt(t, sheets)
        self.yield_refused(t, standing_terms)
        t.term_change = any(standing_terms.get(k, v) != v for k, v in terms_before.items() if k != "unit_price")
        c = safe(lambda: eco.convert(case, standing_terms, t.est, t.terms), standing_terms["unit_price"])
        if t.sent is not None and t.ours:
            st, x = t.sent
            c_norm = t.norm(c)
            observed = status if status in (OPEN, FINAL, AGREED) else OPEN
            safe(lambda: t.belief.update(st, x, observed, c_norm))
            if c > t.ours[-1]:
                t.gap_sum += (c - t.ours[-1]) / c
            t.holds = t.holds + 1 if abs(c - t.theirs[-1]) < 0.005 else 0
            if len(t.ours) == 1:
                t.first_drop = (t.theirs[-1] - c) / t.theirs[-1]
            t.sent = None
        t.theirs.append(c)
        self.bound_full_cost(t, standing_terms)
        t.asks = asks_in(last.get("body"))
        new = supplier.read_signals(t.prof, last.get("body"))
        if "cash_need" in new:
            t.est["interest_rate"] = t.prof["rate"]
        found = reader.result(reading, reader_s - (time.monotonic() - t0))
        new += safe(lambda: self.absorb(t, found, "reply"), []) or []
        self.note(
            t.sid,
            "reply",
            status=status,
            theirs=round(c, 4),
            gap_sum=round(t.gap_sum, 4),
            belief=t.belief.summary() if t.belief else None,
            asks=t.asks,
            signals=new,
            claim_results=last.get("claim_results"),
        )

    def learn_rate(self, t, sheets):
        """Its cost of money from its own priced alternatives, or from which of our round-1
        packages it picked (it answers the sheet that is best for itself). [pc]"""
        terms = [sh["terms"] for sh in sheets]
        std_days = t.std.get("payment_days") or t.est["standard"].get("payment_days")
        rate = supplier.learn_rate(terms, std_days)
        picked_probe = None
        if rate is None and t.probe and terms:
            picked = terms[0].get("payment_days")
            if picked == t.probe["days"]:
                rate = max(t.est["interest_rate"], t.probe["rate"] * 1.1)
                picked_probe = picked
            elif picked == t.probe["main_days"]:
                rate = min(t.est["interest_rate"], t.probe["rate"] * 0.9)
            t.probe = None  # it set a payment term of its own: that says nothing about the pick
        if picked_probe is not None and t.terms.get("payment_days") != picked_probe:
            # it chose our faster-payment package, built at equal C for us: keep it. Holding on to
            # the other one only drew two refusals and then a forced switch, after which our price
            # had to jump to read as a real step from the refused sheet (D02 Demo Drehteile)
            new = {**t.terms, "payment_days": picked_probe}
            if eco.must_haves_ok(self.case, new, t.std):
                self.switch(t, new, "it picked our faster-payment package")
        if rate is None or abs(rate - t.est["interest_rate"]) < 0.005:
            return
        self.note(t.sid, "rate_learned", was=t.est["interest_rate"], now=round(rate, 4))
        t.est["interest_rate"] = rate
        t.prof["rate"] = rate
        if not t.replanned and t.ours:
            t.replanned = True
            ref = eco.at_price(t.terms, t.ours[-1])
            new = safe(lambda: self.plan(t, ref, t.ours[-1]))
            if new and eco.cost(self.case, new[0], t.std, t.est) < eco.cost(self.case, ref, t.std, t.est) * (
                1 - config.ADOPT_MIN_GAIN
            ):
                self.switch(t, new[0], "replanned with its learned cost of money")

    def switch(self, t, new, why):
        """New planned terms; every price we track moves to the same value for the supplier."""
        old = dict(t.terms)

        def move(p):
            return eco.convert(self.case, eco.at_price(old, p), t.est, new)

        # value is affine in the price, so every tracked price and q0 move together: the belief's
        # normalised history (price / q0) stays valid without a rebuild
        t.q0 = move(t.q0)
        t.ours = [move(p) for p in t.ours]
        t.theirs = [move(p) for p in t.theirs]
        t.anchor = move(t.anchor)
        if t.target:
            t.target = move(t.target)
        t.terms = {**new}
        self.note(t.sid, "switch_terms", why=why, terms={k: v for k, v in new.items() if k != "unit_price"})

    def adopt(self, t, sheets):
        """Switch once to a complete package it quoted when it is clearly cheaper for us."""
        if not t.ours:
            return
        case = self.case
        standing_terms = sheets[0]["terms"]
        planned = eco.cost(case, eco.at_price(t.terms, eco.convert(case, standing_terms, t.est, t.terms)), t.std, t.est)
        best = None
        for sh in sheets:
            new = eco.quoted_offer(case, sh["terms"], t.std)
            if new is None or all(new.get(k) == v for k, v in t.terms.items() if k != "unit_price"):
                continue
            c = eco.acceptable(case, new, t.std, t.est)
            if c is not None and c < planned * (1 - config.ADOPT_MIN_GAIN) and (best is None or c < best[0]):
                best = (c, new)
        if best:
            self.switch(t, best[1], "a package it quoted costs us clearly less")

    def yield_refused(self, t, standing_terms):
        """A term it set back to its own value in two replies running is a refusal (P05 refused
        90-day payment eight times): plan with its value so the price talk can go on. Never for the
        index clause or the quality terms, where holding our sheet paid (P18 5.00 vs 5.10). [pc]"""
        if not t.ours:
            return
        refused = {
            f: v
            for f, v in standing_terms.items()
            if f in REFUSABLE and f in t.terms and v is not None and v != t.terms[f]
        }
        for f in list(t.refusals):
            if f not in refused:
                del t.refusals[f]
        for f in refused:
            t.refusals[f] = t.refusals.get(f, 0) + 1
        ripe = {f: v for f, v in refused.items() if t.refusals[f] >= 2}
        opts = self.case.get("term_options") or {}
        ripe = {f: v for f, v in ripe.items() if not opts.get(f) or v in opts[f]}
        if not ripe:
            return
        new = {**t.terms, **ripe}
        if eco.must_haves_ok(self.case, new, t.std):
            for f in ripe:
                t.refusals.pop(f, None)
            self.switch(t, new, f"it refused our {', '.join(sorted(ripe))} twice")

    def bound_full_cost(self, t, terms):
        """A supplier never offers below its cost floor: each sheet bounds our full-cost estimate."""
        full = t.est.get("full_cost")
        if not full:
            return
        offered = safe(lambda: eco.supplier_value(self.case, eco.known_incoterm(self.case, terms), t.est))
        if offered and offered < full:
            t.est["full_cost"] = offered
            if t.target:
                t.target = safe(
                    lambda: eco.relationship_optimum(
                        self.case, t.terms, t.std, t.est, offered * 0.85, max(t.q0, t.theirs[-1] if t.theirs else t.q0)
                    ),
                    t.target,
                )

    # -- one move ----------------------------------------------------------------------------
    def move(self, sid, threads, writer_s=0.0):
        """The next message to this supplier ({"message": ...}), or None when the thread is done."""
        t, events = self.threads[sid], threads[sid]
        last = latest(events)
        status = last.get("status")
        if t.state == "ended" or status in ENDED:
            t.state = "ended"
            return None
        sheets = priced_sheets(last)
        if not sheets:
            t.state = "ended" if not standing(events)[0] else "done"
            return None
        t.std = std_terms(events) or t.std
        key = message_key(events)
        if key != t.last_key:
            t.last_key = key
            t.last_status = status
            if last.get("from") == "arena":  # its answer to our late move: nothing new about it
                t.sent = None
            else:
                t0 = time.monotonic()
                self.read_reply(t, last, sheets, events, min(config.READER_REPLY_S, writer_s))
                writer_s = max(0.0, writer_s - (time.monotonic() - t0))
        if t.state == "mirror":
            t.state = "done"
            return None
        left = moves_left(self.case, events) - t.late
        theirs = t.theirs[-1]
        if status in DONE or left <= 0:
            return self.finish(t, sheets, events, left)
        cap = eco.break_even(self.case, t.terms, t.std, t.est)
        if cap is not None and cap <= 0:
            cap = None  # no price beats the alternative on our estimate: its counters will tell
        if t.ours and cap is not None and t.ours[-1] >= cap:
            t.state = "done"
            self.note(sid, "at_ceiling", cap=cap)
            return None
        if t.ours and t.target and t.full_cost_known and theirs <= t.target:
            t.state = "done"
            self.note(sid, "at_relationship_target", target=t.target, theirs=round(theirs, 2))
            return None
        if t.ours and theirs <= t.ours[-1]:
            return self.finish(t, sheets, events, left)
        price, n_claims = self.next_price(t, sheets, cap, left, threads)
        if price is None:
            t.state = "done"
            return None
        claims = self.pick(t, n_claims, threads)
        main = eco.at_price(t.terms, price)
        extra = safe(lambda: self.extra_sheets(t, main), []) or []
        if self.same_offer(t.last_sent_terms, main) and not extra:
            t.state = "done"
            self.note(sid, "no_progress")
            return None
        return self.send(t, [main, *extra], claims, events, writer_s)

    def next_price(self, t, sheets, cap, left, threads=None):
        """(our next price, claims to cite), or (None, 0) when accepting what stands is better."""
        if not t.ours:
            price = t.anchor if cap is None else min(t.anchor, cap)
            return round(price, 2), config.CLAIMS_FIRST_ROUND
        last = t.ours[-1]
        deep = len(t.ours) == 1 and t.first_drop >= config.DEEP_FIRST_DROP and not t.term_change
        if deep:
            # a job shop's deep first drop: held at the anchor it agreed at our second sheet in
            # every run (Dralveck 27.86, P05 0.828; our 28.25 after a step: 0.788). Token moves
            # after that, not the hold, drew "you only moved a few cents" and a walk. [pc]
            self.note(t.sid, "hold_after_deep_drop", drop=round(t.first_drop, 4))
            return round(last * 1.002, 2), config.CLAIMS_LATER
        st = t.state_now()
        lo, hi = 0.5, 1.1
        grid, values = eco.cost_curve(self.case, t.terms, t.std, t.est, lo * t.q0, hi * t.q0)
        values = [v if math.isfinite(v) else 1e18 for v in values]
        grid = [g / t.q0 for g in grid]
        c_alt = eco.c_alt(self.case)
        accept_cost = eco.acceptable(self.case, sheets[0]["terms"], t.std, t.est)
        open_cost = eco.interp(grid, values, 1.0)
        ctx = planner.Context(
            moves_left=left,
            cost_of=lambda x: eco.interp(grid, values, x),
            walk_cost=c_alt if math.isfinite(c_alt) else open_cost * 1.25,
            cap=t.norm(cap) if cap is not None else None,
            accept_cost=accept_cost,
        )
        decision = safe(lambda: planner.decide(t.belief, st, ctx))
        if decision is None:  # the planner failed: a plain 30 % step
            price = last + config.ROLLOUT_SHARE * (t.theirs[-1] - last)
            price = max(price, last * (1 + config.MIN_MOVE))
        elif decision.kind == "accept" or decision.price is None:
            table = [(round(x * t.q0, 2), round(c, 0), round(w, 3), round(r, 1)) for x, c, w, r in decision.table]
            self.note(t.sid, "planner_accept", expected=round(decision.expected, 2), accept=accept_cost, table=table)
            return None, 0
        else:
            price = decision.price * t.q0
            self.note(
                t.sid,
                "planner",
                price=round(price, 2),
                expected=round(decision.expected, 2),
                table=[(round(x * t.q0, 2), round(c, 0), round(w, 3), round(r, 1)) for x, c, w, r in decision.table],
                belief=t.belief.summary(),
            )
        top = t.theirs[-1] * config.MAX_SHARE_OF_COUNTER
        if cap is not None:
            top = min(top, cap)
        price = round(max(min(price, top), last), 2)
        # the supplier judges the step on the unit price it last read, not on our converted books:
        # after a term switch a real step at equal value can be one cent on paper, and "you only
        # went up by one cent" froze a final offer (D02 Demo Drehteile 13.61 -> 13.62)
        seen = self.seen_price(t)
        if seen:
            real_step = math.ceil(float(seen) * (1 + config.MIN_MOVE) * 100) / 100
            if real_step <= top + 1e-9:
                price = max(price, real_step)
            elif price < real_step:
                self.note(t.sid, "no_real_step", seen=seen, top=round(top, 2))
        if price <= last:
            return None, 0
        return price, config.CLAIMS_LATER

    @staticmethod
    def seen_price(t):
        """The unit price of our last sheet with the terms we plan now (the package it picked),
        else of our main sheet: what the supplier measures our next step against."""
        for sheet in t.last_sent_sheets:
            if all(sheet.get(k) == v for k, v in t.terms.items() if k != "unit_price"):
                return sheet.get("unit_price")
        return (t.last_sent_terms or {}).get("unit_price")

    def finish(self, t, sheets, events, left):
        """Its final word, its agreement or our last message: mirror a cheaper sheet it offered
        itself; if what stands is unacceptable, one last sheet at our ceiling; else done."""
        case = self.case
        c_stand = eco.acceptable(case, sheets[0]["terms"], t.std, t.est)
        alts = []
        for sh in sheets[1:]:
            terms = eco.quoted_offer(case, sh["terms"], t.std)
            c = eco.acceptable(case, terms, t.std, t.est) if terms else None
            if c is not None:
                alts.append((c, terms))
        if alts and left > 0:
            c, terms = min(alts, key=lambda r: r[0])
            if c_stand is None or c < c_stand - 1.0:
                t.state = "mirror"
                self.note(t.sid, "mirror", c=round(c, 2), standing=c_stand)
                return self.send(t, [terms], [], events, 0.0, mirror=True)
        if c_stand is None and left > 0 and t.ours:
            cap = eco.break_even(case, t.terms, t.std, t.est)
            price = min(t.theirs[-1], cap) if cap else t.theirs[-1]
            sheet = eco.at_price(t.terms, max(price, t.ours[-1]))
            if eco.acceptable(case, sheet, t.std, t.est) is not None and not self.same_offer(t.last_sent_terms, sheet):
                t.state = "mirror"
                self.note(t.sid, "last_sheet", price=sheet["unit_price"], cap=cap)
                return self.send(t, [sheet], [], events, 0.0, mirror=True)
        t.state = "done"
        return None

    # -- what goes into the message ----------------------------------------------------------
    def rivals(self, t, threads):
        """Other bidders' standing offers cheaper than this one at our terms, cheapest first, not
        yet cited to this supplier."""
        out = []
        for other, events in threads.items():
            if other == t.sid or latest(events).get("status") in ENDED:
                continue
            oid, terms = standing(events)
            if oid and terms.get("unit_price") and t.theirs and (t.sid, oid) not in self.used:
                price = safe(lambda terms=terms: eco.convert(self.case, terms, t.est, t.terms))
                if price and price < t.theirs[-1]:
                    out.append((price, rival_offer(other, oid, float(terms["unit_price"]))))
        return [c for _, c in sorted(out, key=lambda r: r[0])]

    def pick(self, t, n, threads):
        """Unused claims: in an RFQ the cheapest rival offer first (a new offer every round),
        then this supplier's own arguments, strongest first.

        An ID counts once per case (api.md, What counts), so in an RFQ an argument that is not
        about this supplier (should-cost, forecast, index) can move only one bidder. It is kept
        until every bidder has countered once and then goes to the leader, the one we will most
        likely sign with, all at once. Handed to whoever answered first, it went to the bidder that
        lost in every logged D02 run, and the winner never heard it (34 runs, best 0.746)."""
        rfq = len(threads) > 1
        exclusive_ok = not rfq or self.is_leader(t, threads)
        if rfq and exclusive_ok:
            n = max(n, config.CLAIMS_FIRST_ROUND)
        pool = self.rivals(t, threads)[:1] + t.claims
        out = []
        for c in pool:
            keys = [(t.sid, i) if c.get("rival") else i for i in c["ids"]]
            if any(k in self.used for k in keys) or c in out:
                continue
            if not exclusive_ok and self.exclusive(c, t.sid):
                continue
            out.append(c)
            if len(out) >= n:
                break
        return out

    @staticmethod
    def exclusive(claim, sid):
        """A claim that counts once per case: not a rival's price, not about this supplier only."""
        if claim.get("rival"):
            return False
        short = sid.split(":")[-1]
        return not all(short in i for i in claim["ids"])

    def is_leader(self, t, threads):
        """Every live bidder has countered our first sheet and this one's standing sheet is the
        cheapest acceptable one for us."""
        best = None
        for sid, events in threads.items():
            other = self.threads.get(sid)
            if latest(events).get("status") in ENDED or other is None or other.state == "ended":
                continue
            if len(other.theirs) < 2:
                return False
            oid, terms = standing(events)
            c = self.deal_cost(sid, events, terms) if oid else None
            if c is not None and (best is None or c < best[0]):
                best = (c, sid)
        return best is not None and best[1] == t.sid

    def extra_sheets(self, t, main):
        """Round 1: one package at equal C for us with faster payment, to read what money is worth
        to it (it answers the sheet best for itself). Later: its explicit needs, only when the
        complete package costs us less at equal value to it. Never a looser quality term and never
        an index clause it did not ask for (P11, P18). [pc]"""
        case = self.case
        if "payment_days" not in case["negotiable"]:
            return self.need_sheets(t, main) if t.ours else []
        if not t.ours:
            return self.probe_sheet(t, main)
        return self.need_sheets(t, main)

    def probe_sheet(self, t, main):
        case = self.case
        options = sorted((case.get("term_options") or {}).get("payment_days") or [])
        days = main.get("payment_days")
        faster = [d for d in options if days is not None and d < days]
        if not faster:
            return []
        d = faster[0] if len(faster) == 1 else faster[len(faster) // 2]
        alt = {**main, "payment_days": d}
        c_main = eco.cost(case, main, t.std, t.est)
        lo, hi = 0.0, main["unit_price"]
        if not eco.cost(case, eco.at_price(alt, lo), t.std, t.est) < c_main:
            return []
        for _ in range(50):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if eco.cost(case, eco.at_price(alt, mid), t.std, t.est) < c_main else (lo, mid)
        alt = eco.at_price(alt, math.floor(lo * 100) / 100)
        if alt["unit_price"] < 0.7 * main["unit_price"] or not eco.must_haves_ok(case, alt, t.std):
            return []
        # the rate at which the supplier is indifferent between the two sheets
        lo_r, hi_r = 0.0, config.RATE_BOUNDS[1]
        for _ in range(40):
            r = (lo_r + hi_r) / 2
            est = {**t.est, "interest_rate": r}
            if eco.value(case, alt, est) < eco.value(case, main, est):
                lo_r = r
            else:
                hi_r = r
        t.probe = {"days": d, "main_days": days, "rate": (lo_r + hi_r) / 2}
        self.note(t.sid, "probe_sheet", price=alt["unit_price"], days=d, indifferent_rate=round(t.probe["rate"], 4))
        return [alt]

    def need_sheets(self, t, main):
        signals = set(t.prof.get("signals") or [])
        wanted = set()
        # a frozen price moves only against terms ("any adjustment to the unit price requires a
        # corresponding concession in terms", P03): offer the term trades that cost us least
        frozen = "price_frozen" in signals
        if "payment" in t.asks or "cash_need" in signals or frozen:
            wanted.add(("payment_days", "lower"))
        if "volume" in t.asks or "spare_capacity" in signals or frozen:
            wanted.add(("volume_per_year", "higher"))
        if frozen:
            wanted.add(("contract_months", "higher"))
        if "capacity_full" in signals or "lead_time" in t.asks:
            wanted.add(("lead_time_days", "higher"))
        if "capacity_full" in signals or "contract" in t.asks:
            wanted.add(("contract_months", "higher"))
        case, found = self.case, []
        base_cost, base_value = eco.cost(case, main, t.std, t.est), eco.value(case, main, t.est)
        for field, direction in sorted(wanted):
            if field not in case["negotiable"]:
                continue
            current = main.get(field)
            for v in (case.get("term_options") or {}).get(field) or []:
                if current is None or (v >= current if direction == "lower" else v <= current):
                    continue
                if field == "volume_per_year" and v > case["buyer"]["need_per_year"]:
                    continue
                key = (field, v)
                if key in t.used_trade_keys:
                    continue
                terms = {**main, field: v}
                terms["unit_price"] = round(eco.price_for_value(case, terms, t.est, base_value), 2)
                terms = eco.quoted_offer(case, terms, t.std)
                if terms is None:
                    continue
                c = eco.acceptable(case, terms, t.std, t.est)
                if c is not None and c < base_cost - 1.0 and terms["unit_price"] < t.theirs[-1]:
                    found.append((c, key, terms))
        found.sort(key=lambda r: r[0])
        out = []
        for c, key, terms in found:
            if any(k[0] == key[0] for k, _ in out):
                continue
            out.append((key, terms))
            self.note(t.sid, "need_sheet", field=key[0], value=key[1], cost=round(c, 2), main=round(base_cost, 2))
            if len(out) >= config.EXTRA_SHEETS_MAX:
                break
        for key, _ in out:
            t.used_trade_keys.add(key)
        return [terms for _, terms in out]

    def attachments(self, t):
        """The forecast rows of the part when it asks for proof (a file it reads)."""
        if "proof" not in t.asks:
            return []
        rows = []
        for part in self.case.get("parts") or []:
            for ref in self.data.ids(f"fc:{part.split(':')[-1]}@"):
                f = self.data.fact(ref)
                if f and f.get("volume_per_year") is not None:
                    rows.append((ref.split("@")[-1], part, f["volume_per_year"], f.get("change_pct")))
        if not rows:
            return []
        lines = ["month,part,volume_per_year,change_pct"] + [",".join(str(x) for x in r) for r in rows[-8:]]
        content = base64.b64encode("\n".join(lines).encode()).decode()
        return [{"name": "forecast.csv", "type": "csv", "content_b64": content}]

    def contents(self, t, sheets, claims, first):
        """What this message offers, in words without figures: the writer agent may refer to these
        and nothing else, so its paragraph never promises what the term sheets do not hold."""
        out = []
        main = sheets[0]
        if first:
            out.append("our first proposal")
        elif t.ours and main["unit_price"] > t.ours[-1] + 0.005:
            out.append("a step towards its price")
        fixed = self.case.get("fixed_terms") or {}
        if "volume_per_year" not in self.case["negotiable"] or "volume_per_year" in fixed or first:
            out.append("our binding volume confirmed")
        for alt in sheets[1:]:
            changed = [
                f
                for f in ("payment_days", "volume_per_year", "lead_time_days", "contract_months")
                if alt.get(f) != main.get(f)
            ]
            words = {
                "payment_days": "faster payment"
                if (alt.get("payment_days") or 0) < (main.get("payment_days") or 0)
                else "longer payment",
                "volume_per_year": "a higher binding volume",
                "lead_time_days": "more time to deliver",
                "contract_months": "a longer contract",
            }
            if changed:
                out.append("an alternative package with " + " and ".join(words[f] for f in changed))
        if claims:
            out.append("our data arguments, stated by the system after your paragraph")
        if self.attachments(t):
            out.append("our forecast data attached")
        return out or ["our updated proposal"]

    def send(self, t, sheets, claims, events, writer_s, mirror=False):
        case = self.case
        first = not t.ours
        moved = len(t.theirs) > 2 and t.theirs[-1] < t.theirs[-2] - 0.005
        price = sheets[0]["unit_price"]
        keys = [(t.sid, i) if c.get("rival") else i for c in claims for i in c["ids"]]
        t.pending = {
            "state": t.state if not mirror else "open",
            "keys": keys,
            "primary": dict(sheets[0]),
            "sheets": [dict(s) for s in sheets],
        }
        if not mirror:
            st = t.state_now() if t.theirs else None
            t.sent = (st, t.norm(price)) if st else None
            t.ours.append(price)
            t.pending["appended"] = True
        stmts = writer.statements(case, t.std, sheets, t.asks, first)
        para = None
        if llm.enabled() and writer_s >= config.LLM_MIN_S:
            notes = [f"they asked about: {', '.join(t.asks)}"] if t.asks else []
            notes.append("what our proposal contains: " + "; ".join(self.contents(t, sheets, claims, first)))
            prompt = llm.prompt(case, t.prof, latest(events).get("body"), notes, t.reader)
            t0 = time.monotonic()
            para = llm.paragraph(prompt, writer_s)
            telemetry.emit(
                "llm",
                supplier=t.sid,
                role="writer",
                prompt=telemetry.clip(prompt),
                answer=para,
                seconds=round(time.monotonic() - t0, 2),
                outcome="ok" if para else "none",
            )
        if mirror:
            body = writer.accept_last_sheet_email(case, sheets[0])
        else:
            body = writer.email(case, sheets, claims, stmts, first, moved, t.prof.get("warm", True), para)
        message = {
            "subject": writer.reply_subject(events, case),
            "body": body,
            "term_sheets": [{"terms": s} for s in sheets],
            "refs": sorted({i for c in claims for i in c["ids"]}),
        }
        if att := self.attachments(t):
            message["attachments"] = att
        self.note(
            t.sid,
            "send",
            price=price,
            theirs=round(t.theirs[-1], 2) if t.theirs else None,
            sheets=len(sheets),
            claims=[c["kind"] for c in claims],
            mirror=mirror,
            model=bool(para),
        )
        return {"message": message}

    def fallback_move(self, sid, threads):
        self.note(sid, "fallback")
        return {"message": writer.fallback_message(threads[sid], self.case)}

    def commit(self, sid):
        """The message reached the supplier: its claims are spent."""
        t = self.threads[sid]
        if t.pending:
            self.used.update(t.pending["keys"])
            t.last_sent_terms = t.pending.get("primary")
            t.last_sent_sheets = t.pending.get("sheets") or []
            t.pending = None

    def rollback(self, sid):
        """It came too late (a timeout): the supplier never read it."""
        t = self.threads[sid]
        if t.pending:
            if t.pending.get("appended"):
                t.ours.pop()
                t.sent = None
            t.state = t.pending["state"]
            t.pending = None
        t.late += 1
        self.note(sid, "rollback", late=t.late)

    # -- several bidders and the close -------------------------------------------------------
    def prune(self, threads):
        """A frozen (agreed/final) acceptable sheet elsewhere that beats anything a bidder could
        still reach at our latest sheets: that bidder is done (no rounds, no keepalives)."""
        frozen = []
        for sid, events in threads.items():
            if latest(events).get("status") in DONE:
                oid, terms = standing(events)
                c = self.deal_cost(sid, events, terms) if oid else None
                if c is not None:
                    frozen.append(c)
        if not frozen:
            return
        best = min(frozen)
        for sid, events in threads.items():
            t = self.threads.get(sid)
            if t is None or t.state != "open" or latest(events).get("status") in DONE | ENDED or not t.ours:
                continue
            ours = eco.cost(self.case, eco.at_price(t.terms, t.ours[-1]), t.std, t.est)
            if math.isfinite(ours) and ours >= best:
                t.state, t.dominated = "done", True
                self.note(sid, "dominated", ours=round(ours, 2), frozen=round(best, 2))

    def deal_cost(self, sid, events, terms):
        t = self.threads.get(sid) or Thread(sid)
        return safe(lambda: eco.acceptable(self.case, terms, std_terms(events) or t.std, t.est or None))

    def best_offer(self, threads):
        best = None
        for sid, events in threads.items():
            if latest(events).get("status") in ENDED:
                continue
            oid, terms = standing(events)
            c = self.deal_cost(sid, events, terms) if oid else None
            if c is not None and (best is None or c < best[0]):
                best = (c, sid, oid)
        return best

    def close(self, threads):
        best = self.best_offer(threads)
        if best is None:
            live = [s for s, ev in threads.items() if latest(ev).get("status") not in ENDED] or list(threads)
            self.note(None, "walk_away", c_alt=eco.c_alt(self.case))
            return {"supplier_id": live[0], "message": {"action": {"type": "walk_away"}}}
        self.note(best[1], "accept", c=round(best[0], 2), offer=best[2])
        return {"supplier_id": best[1], "message": {"action": {"type": "accept", "term_sheet_id": best[2]}}}

    @staticmethod
    def same_offer(previous, candidate):
        if not previous or not candidate:
            return False
        if {k: v for k, v in previous.items() if k != "unit_price"} != {
            k: v for k, v in candidate.items() if k != "unit_price"
        }:
            return False
        try:
            return abs(float(previous["unit_price"]) - float(candidate["unit_price"])) <= 0.005
        except (TypeError, ValueError, KeyError):
            return False

    def note(self, sid, what, **kw):
        entry = {"t": round(time.time() - self.started, 1), "supplier": sid, "what": what, **kw}
        self.notes.append(entry)
        telemetry.emit("note", **entry)
        log.info("%s %s %s", self.case.get("case_id"), sid, {k: v for k, v in entry.items() if k not in ("std",)})
