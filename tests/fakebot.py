"""An in-process arena table with suppliers that behave the way the logged hosted bots did:
a smaller step toward a hidden floor every round, a final offer after a token move from us or
once our cumulative gap exhausts its patience, `agreed` as soon as our sheet reaches its next
counter, a walk-away when pushed after its final offer. For guards and flow, not for tuning."""

import itertools

from arena_kit.client import ArenaError
from arena_kit.cost import closing_terms, cost_breakdown, must_haves_ok, score, total_cost


class Bot:
    def __init__(self, sid, quote, floor, share=0.35, patience=0.4, min_move=0.006, std=None, alternatives=None):
        self.sid, self.quote, self.floor, self.share = sid, quote, floor, share
        self.patience, self.min_move = patience, min_move
        self.std = std or {}
        self.alternatives = alternatives  # fn(price) -> list of alternative terms
        self.price, self.last_ours, self.gap, self.status, self.n = quote, None, 0.0, "open", 0
        self.terms = {}
        self.rest = 0.0  # "I give half the step now and check the rest" (P05, a cited rival offer)

    def answer(self, sheets, refs=()):
        rival = any(str(r).startswith("offer:") for r in refs)
        if sheets and self.status == "open":
            pick = max(sheets, key=lambda s: s["unit_price"])
            self.terms = {k: v for k, v in pick.items() if k != "unit_price"}  # it echoes our terms
        if self.rest and self.status == "open" and sheets:  # "Checked: give the rest of the step."
            x = max(s["unit_price"] for s in sheets)
            self.n += 1
            self.price, self.rest = round(max(self.floor, self.price - self.rest), 2), 0.0
            if x >= self.price:
                self.status, self.price = "agreed", x
            self.last_ours = x
            return
        if rival and self.status == "open" and sheets:
            x = max(s["unit_price"] for s in sheets)
            step = self.price - self.floor
            self.n += 1
            self.price, self.rest, self.last_ours = round(self.price - step / 2, 2), step / 2, x
            if x >= self.price:
                self.status, self.price, self.rest = "agreed", x, 0.0
            return
        self._answer(sheets)

    def _answer(self, sheets):
        self.n += 1
        if self.status == "final_offer" and sheets:
            if max(s["unit_price"] for s in sheets) >= self.price - 1e-9:
                pick = max(sheets, key=lambda s: s["unit_price"])
                self.status, self.terms, self.price = "agreed", dict(pick), pick["unit_price"]
                return
            self.status = "walked_away"
            return
        if not sheets or self.status in ("agreed", "walked_away"):
            return
        pick = max(sheets, key=lambda s: s["unit_price"])
        x = pick["unit_price"]
        target = max(self.floor, self.price - self.share * (self.price - self.floor))
        if x >= target:
            self.status, self.terms, self.price = "agreed", dict(pick), x
            return
        moved = self.last_ours is None or (x - self.last_ours) / self.last_ours >= self.min_move
        # a job shop that dropped deep at once reads a held anchor as firmness, not as a token move:
        # "you only moved a few cents, but I want to keep our machines running" (P05) [pc]
        if self.n == 2 and (self.quote - self.price) / self.quote >= 0.06:
            moved = True
        self.last_ours = x
        self.terms = {k: v for k, v in pick.items() if k != "unit_price"}
        if not moved:
            self.status = "final_offer"
            return
        self.price = round(target, 2)
        self.gap += (self.price - x) / self.price
        if self.gap >= self.patience:
            self.status = "final_offer"

    def quote_text(self):
        """A quote PDF's text in the arena's labels (what arena_kit.docs.quote_terms reads)."""
        from arena_kit.docs import LABELS

        names = {f: label for label, f in LABELS.items()}
        lines = ["Quote", "Offered terms", f"Unit price (EUR) {self.price:.2f}", "Our standard terms"]
        lines += [f"{names[k]} {v}" for k, v in self.std.items() if k in names]
        return "\n".join(lines)

    def message(self, case, offer_id):
        sheets = (
            []
            if self.status == "walked_away"
            else [{"term_sheet_id": offer_id, "terms": {**self.terms, "unit_price": self.price}}]
        )
        msg = {
            "from": f"sales@{self.sid.split(':')[1]}",
            "subject": f"Re: {case['title']}",
            "body": "Thank you. Which volume can you commit to? Our offer follows." if self.n == 1 else "Our offer.",
            "status": self.status,
            "term_sheets": sheets,
            "missing_fields": [],
            "claim_results": [],
            "signals": {"reply_delay_days": 2, "validity_days": 30},
            "attachments": [],
            "message_id": f"msg:{self.sid}-{self.n}",
        }
        if self.std and (self.n == 0 or self.status in ("final_offer", "agreed")):  # like the arena
            msg["attachments"] = [
                {"attachment_id": f"att:{offer_id}", "name": "quote-x.pdf", "type": "pdf", "text": self.quote_text()}
            ]
        if self.alternatives and sheets:
            msg["term_sheets"] += [
                {"term_sheet_id": f"{offer_id}-alt{i}", "terms": t} for i, t in enumerate(self.alternatives(self.price))
            ]
        return msg


class FakeTable:
    """Implements what runner.play_case needs from a table."""

    def __init__(self, case, bots, fail_once=None):
        self.case, self.bots, self.session_id, self.done = case, {b.sid: b for b in bots}, "fake1", None
        self.offers, self.ids, self.sent, self.fail_once = {}, itertools.count(1), [], set(fail_once or ())
        self.threads = {}

    def open(self, case_id):
        for sid, b in self.bots.items():
            msg = b.message(self.case, self.offer(sid))
            msg["body"] = "Please find our quote."
            self.threads[sid] = [msg]
        return self.case, {s: list(m) for s, m in self.threads.items()}

    def offer(self, sid):
        oid = f"offer:{sid.split(':')[1]}-{next(self.ids)}"
        self.offers[oid] = sid
        return oid

    def attachment(self, attachment_id):
        return b""

    def send(self, sid, message):
        self.sent.append((sid, message))
        if (sid, len(self.sent)) in self.fail_once or sid in self.fail_once:
            self.fail_once.discard(sid)
            raise ArenaError(409, "late: counted as a timeout")
        for sh in message.get("term_sheets") or []:
            terms = sh["terms"]
            if set(terms) != set(self.case["negotiable"]):
                raise ArenaError(422, f"a term sheet carries exactly the negotiable fields: {sorted(terms)}")
            for f, v in terms.items():
                opts = self.case.get("term_options", {}).get(f)
                if f != "unit_price" and opts and v not in opts:
                    raise ArenaError(422, f"{f}={v!r} not allowed")
        b = self.bots[sid]
        b.answer([sh["terms"] for sh in message.get("term_sheets") or []], message.get("refs") or [])
        reply = b.message(self.case, self.offer(sid))
        self.threads[sid] += [{**message, "from": "buyer"}, reply]
        return reply

    def missed(self, sid, error):
        b = self.bots[sid]
        return {
            "from": "arena",
            "status": b.status,
            "term_sheets": [{"term_sheet_id": self.offer(sid), "terms": {**b.terms, "unit_price": b.price}}],
            "body": f"({error.detail})",
            "missing_fields": [],
            "attachments": [],
        }

    def close(self, sid, message):
        action = message["action"]
        self.sent.append((sid, message))
        if action["type"] == "walk_away":
            self.done = {"deal": False, "score": 0.0, "end_reason": "walked_away"}
            return self.done
        oid = action["term_sheet_id"]
        if self.offers.get(oid) != sid:
            raise ArenaError(422, "unknown or stale term_sheet_id")
        b = self.bots[sid]
        terms = closing_terms(self.case, {**b.terms, "unit_price": b.price}, b.std)
        c_open = min(total_cost(self.case, {"unit_price": x.quote}, x.std) for x in self.bots.values())
        c_best = min(total_cost(self.case, {**terms, "unit_price": x.floor}, x.std) for x in self.bots.values())
        c_deal = total_cost(self.case, terms, b.std)
        self.done = {
            "deal": True,
            "supplier_id": sid,
            "terms": terms,
            "must_haves_ok": must_haves_ok(self.case, terms),
            "c_open": c_open,
            "c_deal": c_deal,
            "c_best": c_best,
            "score": score(c_open, c_deal, c_best),
            "cost_breakdown": cost_breakdown(self.case, terms, b.std),
        }
        return self.done

    def result(self):
        return self.done or {"deal": False}
