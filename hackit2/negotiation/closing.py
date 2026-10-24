"""The end of a thread and the end of a case: echoing a cheaper sheet back, retiring bidders that can
no longer win, and the final accept or walk-away.

`ClosingDesk` is a mixin of `engine.Negotiator`; it relies on `self.case`, `self.suppliers`,
`self.dispatch` and `self.log_event`."""

import functools
import math

from arena_kit.play import ENDED, latest, live_threads, standing

from .. import pricing, settings
from .common import ACTIVE, ECHO, FROZEN, SETTLED, guarded
from .packages import same_offer
from .thread import SupplierThread, quoted_standard_terms


class ClosingDesk:
    def wrap_up(self, thread, sheets, events, counter, messages_left):
        """Its final word, its agreement, or our last message. If one of its alternative sheets costs
        us less than its standing sheet, send that sheet back so it becomes the standing one. If its
        last word is a deal we would refuse, one sheet at the most we would accept costs nothing (a
        refusal and a walk-away both leave us the alternative). Otherwise the thread is settled."""
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
        if standing_cost is None and messages_left > 0 and thread.our_prices:
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
