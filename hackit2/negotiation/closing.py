"""The end of a thread and the end of a case: echoing a cheaper sheet back, retiring bidders that can
no longer win, and the final accept or walk-away.

`ClosingDesk` is a mixin of `engine.Negotiator`; it relies on `self.case`, `self.suppliers`,
`self.dispatch` and `self.log_event`."""

import functools
import math

from arena_kit.play import ENDED, latest, live_threads, standing

from .. import pricing, settings, telemetry
from .common import ACTIVE, ECHO, FINISHED, FROZEN, SETTLED, guarded
from .packages import same_offer
from .thread import SupplierThread, quoted_standard_terms

# The arena ends a thread at its third timeout in a row, or at any timeout after its last round
# (brief "Limits"; the kit's simulator `_tick`). Each full minute a supplier waits for us is a
# timeout and uses a round, even while we write to another bidder.
TIMEOUTS_TO_END = 3


def clock_state(left_messages, replied_at, silent_rounds, streak, now, clock_s=None):
    """A waiting thread's clock as the arena counts it: (rounds left, timeouts in a row, seconds to
    the next timeout). `left_messages`: our messages left by our own count; `silent_rounds`: timeouts
    of earlier waits that our count does not hold; `streak`: timeouts in a row before this wait."""
    clock_s = clock_s or settings.THREAD_CLOCK_S
    age = max(0.0, now - replied_at)
    waited = int(age // clock_s)  # timeouts during this wait
    return left_messages - silent_rounds - waited, streak + waited, clock_s * (waited + 1) - age


def ends_at_next_timeout(rounds_left, in_a_row):
    """True when the thread's next timeout ends it: its last round is used, or two came in a row."""
    return rounds_left <= 1 or in_a_row >= TIMEOUTS_TO_END - 1


class ClosingDesk:
    @telemetry.traced("negotiation.closing.wrap_up", result_view=telemetry.message_view)
    def wrap_up(self, thread, sheets, events, counter, messages_left):
        """Its final word, its agreement, or our last message. If one of its alternative sheets costs
        us less than its standing sheet, send that sheet back so it becomes the standing one. If its
        last word freezes the offer, only its own sheets may be echoed. While it is still bargaining,
        one sheet at the most we would accept can answer a deal we would refuse. Otherwise settle."""
        case, standard = self.case, thread.standard
        standing_cost = pricing.acceptable_cost(case, sheets[0]["terms"], standard, thread.valuation)
        alternatives = []
        for sheet in sheets[1:]:
            package = pricing.exact_package(case, sheet["terms"], standard)
            c = pricing.acceptable_cost(case, package, standard, thread.valuation) if package is not None else None
            if c is not None:
                alternatives.append((c, package))
        if alternatives and messages_left > 0:
            c, package = min(alternatives, key=lambda row: row[0])
            if standing_cost is None or c < standing_cost - settings.MIRROR_MIN_SAVING_EUR:
                thread.phase = ECHO
                return self.dispatch(thread, [package], [], events, counter)
        if (
            standing_cost is None
            and messages_left > 0
            and thread.our_prices
            and latest(events).get("status") not in FROZEN
        ):
            ceiling = pricing.price_ceiling(case, thread.plan, standard, thread.valuation)
            last = pricing.priced(thread.plan, min(counter, ceiling) if ceiling is not None else counter)
            acceptable = pricing.acceptable_cost(case, last, standard, thread.valuation) is not None
            if acceptable and not same_offer(thread.last_primary_sent, last):
                thread.phase = ECHO
                self.log_event(
                    thread.supplier_id,
                    "last_acceptable_sheet",
                    price=last["unit_price"],
                    counter=counter,
                    ceiling=ceiling,
                )
                return self.dispatch(thread, [last], [], events, counter)
        thread.phase = SETTLED
        return None

    def standing_cost(self, supplier_id, events, terms):
        thread = self.suppliers.get(supplier_id) or SupplierThread(supplier_id)
        return pricing.acceptable_cost(
            self.case, terms, quoted_standard_terms(events) or thread.standard, thread.valuation
        )

    @telemetry.traced("negotiation.closing.retire_outclassed")
    def retire_outclassed(self, threads):
        """With several bidders, an agreed or final sheet no longer moves and our sheets to the others
        only rise. A bidder whose deal at our latest sheets would cost at least the cheapest acceptable
        frozen sheet cannot improve the case: it is settled, so it stops taking turns on the session's
        shared clock (the arena serves one call per session at a time)."""
        frozen = []
        for supplier_id, events in threads.items():
            if latest(events).get("status") in FROZEN:
                offer_id, terms = standing(events)
                c = guarded(functools.partial(self.standing_cost, supplier_id, events, terms)) if offer_id else None
                if c is not None:
                    frozen.append(c)
        if not frozen:
            return
        best = min(frozen)
        for supplier_id, events in threads.items():
            thread = self.suppliers.get(supplier_id)
            if thread is None or thread.phase != ACTIVE or latest(events).get("status") in FROZEN | ENDED:
                continue
            ours = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
            if not ours:
                continue
            lowest = min(
                pricing.buyer_cost(self.case, sheet.get("terms") or {}, thread.standard, thread.valuation)
                for sheet in ours[-1]["term_sheets"]
            )
            if math.isfinite(lowest) and lowest >= best:
                thread.phase, thread.outclassed = SETTLED, True
                self.log_event(supplier_id, "outclassed", ours=round(lowest, 2), frozen=round(best, 2))

    def rival_could_beat(self, threads, best_supplier, best_cost):
        """Another bidder still bargaining that could end below `best_cost`, or None. A supplier does
        not go below what we asked, so our latest sheets to it bound its best deal (as in
        `retire_outclassed`); a bidder we have not offered anything yet could still beat it."""
        for supplier_id, events in threads.items():
            thread = self.suppliers.get(supplier_id)
            if supplier_id == best_supplier or thread is None or thread.phase in FINISHED or thread.outclassed:
                continue
            if latest(events).get("status") in FROZEN | ENDED:
                continue  # a frozen sheet no longer moves: best_standing_offer already compared it
            ours = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
            if not ours:
                return supplier_id
            lowest = min(
                pricing.buyer_cost(self.case, sheet.get("terms") or {}, thread.standard, thread.valuation)
                for sheet in ours[-1]["term_sheets"]
            )
            if lowest < best_cost:
                return supplier_id
        return None

    def other_acceptable_sheet(self, threads, supplier_id):
        """Does another live thread hold an acceptable standing sheet?"""
        for other, events in threads.items():
            if other == supplier_id or latest(events).get("status") in ENDED:
                continue
            offer_id, terms = standing(events)
            if offer_id and guarded(functools.partial(self.standing_cost, other, events, terms)) is not None:
                return True
        return False

    @telemetry.traced("negotiation.closing.insurance")
    def insurance(self, threads, clocks, now, serve_s):
        """RFQ insurance: the cheapest acceptable standing sheet must not die with its thread while we
        bargain elsewhere. `clocks`: supplier -> (when its last reply came, timeouts of earlier waits
        our count does not hold, timeouts in a row before this wait); `serve_s`: how long our next move
        to another bidder takes (model plus round trip).

        Acts only when that sheet's thread waits settled, its next timeout ends it, and the timeout
        comes before we could serve the thread after one more move elsewhere. Then:
        - ("accept", supplier) when no other bidder can still beat the sheet, or when no other
          acceptable sheet stands (a bad deal beats no deal);
        - ("keepalive", supplier) when a round is left: it restarts the clock (P05: the winning bidder
          waited through two timeouts in a row while we bargained with the others);
        - None otherwise: a rival can still beat the sheet and holds an acceptable sheet itself."""
        best = self.best_standing_offer(threads)
        if best is None:
            return None
        cost, supplier_id, offer_id = best
        thread = self.suppliers.get(supplier_id)
        if thread is None or thread.phase != SETTLED or supplier_id not in clocks:
            return None  # a thread still bargaining is served by the loop itself
        rounds_left, in_a_row, next_timeout_s = clock_state(
            self.messages_left(thread, threads[supplier_id]), *clocks[supplier_id], now
        )
        if not ends_at_next_timeout(rounds_left, in_a_row) or next_timeout_s - settings.CLOCK_RESERVE_S > serve_s:
            return None
        details = {"offer": offer_id, "c": round(cost, 2), "rounds_left": rounds_left, "in_a_row": in_a_row}
        rival = self.rival_could_beat(threads, supplier_id, cost)
        if rival is None or (rounds_left < 1 and not self.other_acceptable_sheet(threads, supplier_id)):
            why = "no other bidder can beat it" if rival is None else "no other acceptable sheet stands"
            self.log_event(
                supplier_id, "insurance_accept", reason=f"its thread ends at the next timeout; {why}", **details
            )
            return ("accept", supplier_id)
        if rounds_left >= 1:
            self.log_event(supplier_id, "insurance_keepalive", rival=rival, **details)
            return ("keepalive", supplier_id)
        noted = self.__dict__.setdefault("_insurance_skipped", set())
        if offer_id not in noted:  # logged once, not on every turn of the loop
            noted.add(offer_id)
            self.log_event(supplier_id, "insurance_skipped", rival=rival, **details)
        return None

    @telemetry.traced("negotiation.closing.best_standing_offer")
    def best_standing_offer(self, threads):
        """(C, supplier, term_sheet_id) of the cheapest acceptable standing sheet. Only a standing sheet
        can be accepted; alternatives were echoed back to become standing first. Broken data on one
        supplier skips that supplier, never the close."""
        best = None
        for supplier_id, events in threads.items():
            if latest(events).get("status") in ENDED:
                continue
            offer_id, terms = standing(events)
            c = guarded(functools.partial(self.standing_cost, supplier_id, events, terms)) if offer_id else None
            if c is not None and (best is None or c < best[0]):
                best = (c, supplier_id, offer_id)
        return best

    @telemetry.traced("negotiation.closing.final_decision", result_view=telemetry.decision_view)
    def final_decision(self, threads):
        """Accept the cheapest acceptable standing sheet, or walk away."""
        best = self.best_standing_offer(threads)
        if best is None:
            live = live_threads(threads) or list(threads)
            self.log_event(None, "walk_away")
            return {"supplier_id": live[0], "message": {"action": {"type": "walk_away"}}}
        c, supplier_id, offer_id = best
        self.log_event(supplier_id, "accept", c=c, offer=offer_id)
        return {"supplier_id": supplier_id, "message": {"action": {"type": "accept", "term_sheet_id": offer_id}}}
