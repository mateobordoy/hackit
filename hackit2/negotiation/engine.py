"""The negotiation engine, one instance per case and one `SupplierThread` per supplier.

Per thread it plans the non-price terms by C at the supplier's own valuation, opens below the quote,
then steps toward the supplier's counter round by round so it keeps conceding, spreading verified
arguments over the rounds. A thread ends on the supplier's final offer, its agreement or the round
limit; in relationship goals it stops at the price where C is lowest.

What the suppliers showed in sandbox and practice:
- they concede only after a move from us and agree as soon as our sheet reaches the counter they
  would make, so our sheet stays below their counter until the end;
- every verified argument they accept lowers where they stop, most of it in round one;
- patience runs out at a cumulative gap of roughly 40 % between our sheets and their counters, much
  earlier for a supplier selling near its cost or one that is fully booked;
- a large first concession means a deep floor: then we hold low instead of meeting it."""

import base64
import time

from arena_kit.play import ENDED, latest, moves_left, standing

from .. import adviser_priorities, mailer, model, pricing, reply_reader, settings, telemetry, transcript
from ..evidence import EvidenceBank, live_rival_offer
from ..supplier_profile import build_profile, neutral_profile, traits_in
from .closing import ClosingDesk
from .common import ACTIVE, ECHO, FROZEN, GONE, SETTLED, guarded, log
from .concession import next_step_price
from .packages import PackageDesk, argument_families, counters_stalled, same_offer
from .thread import SupplierThread, detect_requests, quoted_standard_terms

MOOD_HINTS = {
    "irritated": "they sound irritated: acknowledge it sincerely before anything else",
    "pressured": "they sound under pressure: stay calm and make the next step easy for them",
}


class Negotiator(PackageDesk, ClosingDesk):
    def __init__(self, case, pkg=None, complete=None, briefs=None):
        """`briefs`: the preparation team's brief per supplier (`prep.briefing.prepare_briefs`); None
        plays the plain price engine, with no prepared strategy and no reply reader."""
        self.case, self.pkg, self.complete, self.briefs = case, pkg, complete, briefs
        self.evidence_bank = EvidenceBank(pkg, case)
        self.suppliers = {}
        self.cited = set()  # refs cited in this case (a rival quote: per supplier)
        self.journal = []  # every decision, for the run log
        self.live_journal = None  # optional path: every decision is also appended there as it happens
        self.write_budget_s = settings.MODEL_TIMEOUT_S  # model time left for the move being built

    # --- opening a thread -----------------------------------------------------------------------

    @telemetry.traced("negotiation.engine.open_thread", result_view=telemetry.thread_view)
    def open_thread(self, supplier_id, events):
        case, thread = self.case, SupplierThread(supplier_id)
        thread.standard = guarded(lambda: quoted_standard_terms(events), {}) or {}
        offer_id, quote = standing(events)
        thread.profile = guarded(lambda: build_profile(self.pkg, supplier_id), neutral_profile())
        self.absorb_traits(thread, latest(events).get("body"))
        brief = (self.briefs or {}).get(supplier_id) or {}
        self.suppliers[supplier_id] = thread
        if not offer_id or quote.get("unit_price") is None:
            thread.phase = GONE
            return thread
        thread.last_counter_terms = quote
        parts = case.get("parts") or []
        # a bundle has no should-cost: one component's cost is not the price unit of the case
        full_cost, own_plant = (None, False)
        if len(parts) == 1:
            full_cost, own_plant = guarded(lambda: self.supplier_full_cost(parts[0], supplier_id), (None, False))
        quoted_lead = quote.get("lead_time_days") or thread.standard.get("lead_time_days")
        fully_booked = "capacity_full" in thread.profile["traits"]
        rush = settings.RUSH_COST_FULLY_BOOKED if fully_booked else settings.RUSH_COST_DEFAULT
        thread.valuation = pricing.build_valuation(
            case,
            thread.standard or quote,
            thread.profile["money_rate"],
            full_cost,
            guarded(lambda: self.delivered_ppm(supplier_id)),
            quoted_lead,
            rush,
        )
        quoted_price = quote["unit_price"]
        goal, traits = case["buyer"].get("category_goal"), thread.profile["traits"]
        sustainable = case["buyer"].get("sustainable_margin_pct") or 0
        relationship = goal in pricing.RELATIONSHIP_GOALS
        if full_cost and relationship:
            # the quote includes its margin, so a should-cost above that is our estimate's error
            full_cost = min(full_cost, pricing.supplier_worth(case, quote, thread.valuation) * (1 - sustainable / 100))
            thread.valuation["full_cost"] = full_cost
        share = settings.OPENING_SHARE.get(goal, settings.OPENING_SHARE_DEFAULT)
        near_cost = bool(own_plant and full_cost >= settings.NEAR_COST_RATIO * quoted_price)
        if relationship:
            thread.patience = settings.PATIENCE_RELATIONSHIP
        if "price_frozen" in traits:
            share = max(share, settings.OPENING_SHARE_FROZEN)  # its list price barely moves; a deep cut costs patience
        if fully_booked:
            share = max(share, settings.OPENING_SHARE_FULLY_BOOKED)
            thread.patience = min(thread.patience, settings.PATIENCE_FULLY_BOOKED)
        if near_cost:
            share = max(share, settings.OPENING_SHARE_NEAR_COST)
            thread.patience = min(thread.patience, settings.PATIENCE_NEAR_COST)
        current = case["buyer"].get("current_unit_price")
        if case.get("type") == "price_increase" and current:
            share = min(share, current / quoted_price)  # a price increase opens at today's price at most
        share = max(settings.OPENING_SHARE_MIN, share)
        if brief:
            self.apply_brief(thread, brief)
        # terms are planned at the opening price: coverage pays off at a low price, not at the quote
        opening = quoted_price * share
        planned = guarded(lambda: pricing.cheapest_terms(case, thread.standard, thread.valuation, quote, opening))
        thread.plan = planned[0] if planned else pricing.complete_offer(case, pricing.priced(quote, opening))
        thread.opening_price = thread.plan["unit_price"]
        thread.quote_at_plan = guarded(
            lambda: round(
                pricing.price_for_worth(
                    case, thread.plan, thread.valuation, pricing.supplier_worth(case, quote, thread.valuation)
                ),
                2,
            ),
            quoted_price,
        )
        if full_cost:
            # below the sweet spot a lower price costs us more than it saves, and pushing on can make
            # a supplier selling near cost withdraw
            thread.sweet_spot = guarded(
                lambda: pricing.relationship_sweet_spot(
                    case,
                    thread.plan,
                    thread.standard,
                    thread.valuation,
                    full_cost * settings.SWEET_SPOT_FLOOR_SHARE,
                    thread.quote_at_plan,
                )
            )
        guarded(lambda: self.offer_index_clause(thread, quote, full_cost))
        thread.evidence = guarded(lambda: self.evidence_bank.for_supplier(supplier_id, quoted_price), [])
        order = brief.get("argument_order") or []  # arguments the lead expects this supplier to accept, first
        thread.evidence.sort(key=lambda a: order.index(a["topic"]) if a["topic"] in order else len(order))
        self.log_event(
            supplier_id,
            "open_thread",
            quote=quote,
            standard=thread.standard,
            profile=thread.profile,
            full_cost=full_cost,
            near_cost=near_cost,
            plan=thread.plan,
            quote_at_plan=thread.quote_at_plan,
            opening=thread.opening_price,
            sweet_spot=thread.sweet_spot,
            patience=thread.patience,
            evidence=[a["sentence"] for a in thread.evidence],
        )
        return thread

    def apply_brief(self, thread, brief):
        """The preparation team's brief: prepared trades wait for our opening sheet, the playbook for
        the supplier's arguments, notes and tone go to the e-mail writer. Prices and pace stay with
        the engine: a model moving them cost score."""
        thread.prep_notes = brief.get("writer_notes") or ""
        thread.prep_trades = brief.get("trades") or []
        thread.playbook = brief.get("playbook") or {}
        if brief.get("tone"):
            thread.profile["warm"] = brief["tone"] == "warm"
        self.log_event(
            thread.supplier_id,
            "brief",
            trades=thread.prep_trades,
            rationale=brief.get("rationale"),
            playbook=thread.playbook,
            findings=brief.get("findings"),
        )

    def supplier_full_cost(self, part, supplier_id):
        """(should-cost of the part, whether it models this supplier's own plant): full cost without
        margin. A generic estimate on a German cost base says little about one supplier."""
        rows = self.evidence_bank.query("SELECT id, supplier_id, unit_cost FROM should_cost WHERE part_id = ?", (part,))
        own = [r for r in rows if r.get("supplier_id") == supplier_id]
        if own:
            return own[0]["unit_cost"], True
        generic = [r for r in rows if not r.get("supplier_id")]
        if generic:
            return generic[0]["unit_cost"], False
        for ref in self.evidence_bank.ids("cost:"):  # the case's own facts, when the tables have no row
            row = self.evidence_bank.fact(ref)
            if row and row.get("part") == part and row.get("unit_cost"):
                return row["unit_cost"], row.get("supplier") == supplier_id
        return None, False

    def delivered_ppm(self, supplier_id):
        """The defect rate it actually delivers (latest scorecard): what a chargeback really costs it."""
        refs = self.evidence_bank.ids(f"sc:{supplier_id.split(':')[-1]}@")
        row = self.evidence_bank.fact(refs[-1]) if refs else None
        return row.get("ppm") if row else None

    @telemetry.traced("negotiation.engine.resume_thread")
    def resume_thread(self, supplier_id, events):
        """A resumed session (crash, restart): rebuild what we sent and what it answered from the thread,
        so the next move continues the path instead of opening again."""
        thread = self.suppliers[supplier_id]
        ours = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
        if thread.phase == GONE or not ours:
            return
        thread.plan = pricing.complete_offer(self.case, ours[-1]["term_sheets"][0]["terms"])
        counter = None
        for message in events:
            if message.get("from") == "buyer":
                if adviser_priorities.was_asked(message.get("body")):
                    thread.adviser_inquiry_sent = True
                refs = message.get("refs") or []
                self.cited.update(refs)
                thread.used_topics.update(
                    family for ref in refs for family in argument_families({"topic": "forecast", "refs": [ref]})
                )
                if message.get("term_sheets"):
                    sent = [sheet.get("terms") or {} for sheet in message["term_sheets"]]
                    thread.last_primary_sent = dict(sent[0])
                    thread.shown_alternatives.update(self.alternative_keys(sent))
                    price = self.price_at_plan(thread, message["term_sheets"][0]["terms"])
                    thread.our_prices.append(price)
                    thread.gap_history.append(max(0.0, (counter - price) / counter) if counter else 0.0)
            else:
                if settings.SELLER_ADVISER_INQUIRY:
                    thread.adviser_priorities = adviser_priorities.update(
                        thread.adviser_priorities, message.get("body")
                    )
                sheets = message.get("term_sheets") or []
                if sheets and sheets[0].get("terms", {}).get("unit_price") is not None:
                    counter = self.price_at_plan(thread, sheets[0]["terms"])
                    if thread.our_prices:
                        thread.their_prices.append(counter)
        if events and events[-1].get("from") != "buyer" and thread.their_prices:
            thread.their_prices.pop()  # `next_message` reads the latest reply like any fresh one
        self.log_event(supplier_id, "resume", ours=thread.our_prices, theirs=thread.their_prices, plan=thread.plan)

    # --- reading a reply ------------------------------------------------------------------------

    def absorb_traits(self, thread, body):
        """Traits in its own e-mail (a supplier the package does not know, or news since then) join the
        profile; a cash need raises what money is worth to it."""
        new = [t for t in traits_in(body) if t not in thread.profile["traits"]]
        if not new:
            return
        thread.profile["traits"] = sorted(thread.profile["traits"] + new)
        if "cash_need" in new:
            thread.profile["money_rate"] = round(thread.profile["money_rate"] + settings.CASH_NEED_RATE_BONUS, 3)
            if thread.valuation:
                thread.valuation["interest_rate"] = thread.profile["money_rate"]
        self.log_event(thread.supplier_id, "traits", new=new)

    def tighten_full_cost(self, thread, terms):
        """A supplier never offers below its cost floor, so every sheet it sends, valued to it, caps our
        full-cost estimate (should-cost tends to overshoot by a percent or two)."""
        full_cost = thread.valuation.get("full_cost")
        if not full_cost:
            return
        offered = pricing.supplier_worth(self.case, terms, thread.valuation)
        if offered < full_cost:
            thread.valuation["full_cost"] = offered
            self.log_event(thread.supplier_id, "full_cost_capped", offered=round(offered, 2), was=round(full_cost, 2))

    @telemetry.traced("negotiation.engine.consult_reader")
    def consult_reader(self, thread, body):
        """The reply reader on its latest e-mail: the mood goes to the writer, the tactic paces our next
        price and evidence, the matched argument picks our prepared answer, and its requests join the
        ones the patterns found. Its time comes out of the writer's; when late or off, the move goes on
        with the patterns alone."""
        thread.mood, thread.tactic, thread.matched_argument = None, None, 0
        if self.write_budget_s < settings.READER_MIN_CLOCK_S:  # several threads share the clocks
            return
        started = time.monotonic()
        expected = thread.playbook.get("arguments") or []
        reading = reply_reader.read_reply(
            self.complete,
            self.case,
            body,
            thread.our_prices,
            thread.their_prices,
            expected,
            self.write_budget_s / 2,
            history=thread.history,
            supplier_id=thread.supplier_id,
        )
        self.write_budget_s -= time.monotonic() - started
        if reading is None:
            return
        thread.requests = [k for k in reply_reader.REQUEST_KINDS if k in thread.requests or k in reading["requests"]]
        thread.mood, thread.tactic = reading["mood"], reading["tactic"]
        thread.matched_argument, thread.reader_notes = reading["argument"], reading["notes"]
        thread.tactic_log.append(thread.tactic)
        self.log_event(thread.supplier_id, "reader", seconds=round(time.monotonic() - started, 1), **reading)

    def briefing_facts(self, thread, first, with_alternatives):
        """What the team adds to the writer's facts: our prepared answer to the argument its e-mail made,
        a point to raise in the opening, the alternatives on offer, the mood to meet."""
        facts = []
        answers = thread.playbook.get("answers") or []
        number = thread.matched_argument
        if number and number <= len(answers) and answers[number - 1]:
            facts.append(
                f'they argue: "{thread.playbook["arguments"][number - 1]}" Our answer, in your own words: '
                f"{answers[number - 1]}"
            )
        if first and thread.playbook.get("raise"):
            facts.append(f"a point to raise politely, once: {thread.playbook['raise'][0]}")
        if with_alternatives:
            why = "; ".join(t["why"] for t in [*thread.prep_trades, *thread.need_trades] if t.get("why"))
            facts.append(
                "our proposal lists alternative packages after the first one: say they are free to choose the one "
                f"that suits them best ({why})"
            )
        elif thread.need_trades:
            why = "; ".join(t["why"] for t in thread.need_trades if t.get("why"))
            facts.append(f"we adjusted the package in response to {why}; explain the commercial terms clearly")
        if thread.mood in MOOD_HINTS:
            facts.append(MOOD_HINTS[thread.mood])
        if settings.SELLER_ADVISER_INQUIRY and not first:
            facts += adviser_priorities.writer_facts(thread.adviser_priorities)
        return facts

    def observe_adviser_priorities(self, thread, body):
        """Supplier text provides directions to evaluate, never prices, commitments or approvals."""
        if not settings.SELLER_ADVISER_INQUIRY or not thread.our_prices or not thread.adviser_inquiry_sent:
            return
        previous = thread.adviser_priorities
        thread.adviser_priorities = adviser_priorities.update(previous, body)
        hinted = adviser_priorities.requests(thread.adviser_priorities)
        thread.requests = [kind for kind in reply_reader.REQUEST_KINDS if kind in thread.requests or kind in hinted]
        if previous != thread.adviser_priorities:
            self.log_event(thread.supplier_id, "adviser_priorities", priorities=thread.adviser_priorities)

    def price_at_plan(self, thread, terms):
        """Its sheet converted to a price at our planned terms (same worth to the supplier)."""
        if all(terms.get(k) == v for k, v in thread.plan.items() if k != "unit_price" and k in terms):
            return terms["unit_price"]
        worth = pricing.supplier_worth(self.case, terms, thread.valuation)
        return round(pricing.price_for_worth(self.case, thread.plan, thread.valuation, worth), 2)

    def _refresh_sweet_spot(self, thread, previous):
        floor = thread.valuation["full_cost"] * settings.SWEET_SPOT_FLOOR_SHARE
        thread.sweet_spot = pricing.relationship_sweet_spot(
            self.case,
            thread.plan,
            thread.standard,
            thread.valuation,
            floor,
            max(floor, thread.quote_at_plan, previous, thread.their_prices[-1]),
        )

    # --- one move -------------------------------------------------------------------------------

    @telemetry.traced("negotiation.engine.next_message", result_view=telemetry.message_view)
    def next_message(self, supplier_id, threads, write_budget_s=None):
        """The next message to this supplier as {"message": ...}, or None when the thread is finished.
        `write_budget_s`: what the supplier clocks leave for the model."""
        thread, events = self.suppliers[supplier_id], threads[supplier_id]
        thread.history = transcript.build(self.case, events)
        self.write_budget_s = settings.MODEL_TIMEOUT_S if write_budget_s is None else write_budget_s
        thread.phase_before = thread.phase
        last = latest(events)
        status = last.get("status")
        if thread.phase == GONE or status in ENDED:
            thread.phase = GONE
            return None
        sheets = [s for s in last.get("term_sheets") or [] if s.get("terms", {}).get("unit_price") is not None]
        if not sheets:
            thread.phase = GONE if not standing(events)[0] else SETTLED
            return None
        standard_before = thread.standard
        thread.standard = quoted_standard_terms(events) or thread.standard
        if thread.last_seen_reply is not last:
            plan_before, valuation_before = dict(thread.plan), dict(thread.valuation)
            self.adopt_cheaper_package(thread, sheets)
            counter_terms = sheets[0]["terms"]
            thread.counter_changed_terms = any(
                counter_terms.get(k, v) != v for k, v in plan_before.items() if k != "unit_price"
            )
            thread.their_prices.append(self.price_at_plan(thread, counter_terms))
            self.tighten_full_cost(thread, counter_terms)
            thread.last_counter_terms = counter_terms
            thread.last_seen_reply = last
            thread.requests = detect_requests(last.get("body"))
            self.absorb_traits(thread, last.get("body"))
            if self.briefs is not None and status not in FROZEN:
                self.consult_reader(thread, last.get("body"))
            self.observe_adviser_priorities(thread, last.get("body"))
            changed = (
                thread.plan != plan_before or thread.valuation != valuation_before or thread.standard != standard_before
            )
            if thread.sweet_spot is not None and changed:
                self._refresh_sweet_spot(thread, thread.sweet_spot)
        counter = thread.their_prices[-1]
        if thread.phase == ECHO:  # it has seen its own sheet again: nothing more to do
            thread.phase = SETTLED
            return None
        left = self.messages_left(thread, events)
        if status in FROZEN or left <= 0:
            return self.wrap_up(thread, sheets, events, counter, left)
        ceiling = pricing.price_ceiling(self.case, thread.plan, thread.standard, thread.valuation)
        if ceiling is not None and ceiling <= 0:
            # no price beats the alternative on our estimate: bargain on without a ceiling, since its
            # counters cap the estimate and the close still refuses a deal above the alternative
            ceiling = None
        if ceiling is not None and thread.our_prices and thread.our_prices[-1] > ceiling:
            thread.phase = SETTLED
            self.log_event(supplier_id, "no_room_below_alternative", ceiling=ceiling)
            return None
        if thread.our_prices and thread.sweet_spot and counter <= thread.sweet_spot:
            # it already offers at or below our estimated optimum: let the close judge its standing sheet
            thread.phase = SETTLED
            return None
        if thread.our_prices and counter <= thread.our_prices[-1]:  # it came below our own offer: take it
            return self.wrap_up(thread, sheets, events, counter, left)
        price, argument_count = self.next_offer_price(thread, counter)
        if ceiling is not None and price > ceiling:
            price = max(ceiling, thread.our_prices[-1]) if thread.our_prices else ceiling  # never back down
            self.log_event(supplier_id, "capped_at_alternative", ceiling=ceiling, price=price)
        arguments = self.choose_arguments(thread, argument_count, threads)
        sheets = [pricing.priced(thread.plan, price)]
        probes = []
        if not thread.our_prices and thread.prep_trades:
            sheets += guarded(lambda: self.prepared_alternatives(thread, sheets[0]), [])[: settings.PREP_TRADES_SHOWN]
        elif thread.our_prices:
            sheets += guarded(lambda: self.need_alternatives(thread, sheets[0]), [])[: settings.NEED_TRADES_MAX]
            slots = settings.NEED_TRADES_MAX - len(sheets) + 1
            if settings.SELLER_ADVISER_INQUIRY and thread.adviser_inquiry_sent and slots > 0:
                covered = {
                    field
                    for sheet in sheets[1:]
                    for field in self.case["negotiable"]
                    if field != "unit_price" and sheet.get(field) != sheets[0].get(field)
                }
                probes = guarded(lambda: self.value_discovery_alternatives(thread, sheets[0], covered, slots), [])
                sheets += probes
        if not arguments and len(sheets) == 1 and counters_stalled(events):
            standing_sheets = latest(events).get("term_sheets") or []
            if any(
                pricing.acceptable_cost(self.case, s.get("terms") or {}, thread.standard, thread.valuation) is not None
                for s in standing_sheets
            ):
                self.log_event(
                    supplier_id, "supplier_stalled", reason="three unchanged counters; no fresh argument or alternative"
                )
                return self.wrap_up(thread, standing_sheets, events, counter, left)
        switched_keys = set()
        if same_offer(thread.last_primary_sent, sheets[0]):
            if len(sheets) == 1:
                thread.phase = SETTLED
                self.log_event(supplier_id, "no_progress", reason="unchanged price and terms")
                return None
            if sheets[1] in probes:
                # A probe is not evidence the supplier selected that package. Never replan merely
                # to force an extra sheet out after our primary price has stopped progressing.
                thread.phase = SETTLED
                self.log_event(supplier_id, "probe_not_adopted", reason="unchanged primary; no seller selection")
                return None
            # never resend the old main sheet: move the thread onto the best new package, which costs us
            # less at the same estimated worth to the supplier
            switched_keys = self.alternative_keys(sheets)
            chosen = sheets[1]
            previous_spot = thread.sweet_spot
            self.replan(thread, chosen, "cost-checked response to a supplier need")
            if previous_spot is not None and thread.valuation.get("full_cost"):
                self._refresh_sweet_spot(thread, previous_spot)
            sheets = [chosen]
        message = self.dispatch(thread, sheets, arguments, events, counter)
        if thread.in_flight and switched_keys:
            thread.in_flight["alternative_keys"].update(switched_keys)
        return message

    def messages_left(self, thread, events):
        """Messages we may still send: the arena counts a late message as a round too."""
        return moves_left(self.case, events) - thread.late_rounds

    @telemetry.traced("negotiation.engine.next_offer_price")
    def next_offer_price(self, thread, counter):
        """(our next price at the plan, how many arguments to cite): the opening first, then a share of
        the gap, held after a deep first concession and kept within the supplier's patience."""
        if not thread.our_prices:
            return thread.opening_price, settings.CLAIMS_FIRST_ROUND
        last = thread.our_prices[-1]
        prices = thread.their_prices
        first_drop = (prices[-2] - counter) / prices[-2] if len(prices) > 1 else 0.0
        share = settings.STEP_SHARE * (settings.HOLD_FACTOR if thread.tactic == "hold" else 1.0)
        price = next_step_price(
            last,
            counter,
            first_drop,
            len(thread.our_prices) == 1,
            share,
            thread.patience,
            sum(thread.gap_history),
            thread.counter_changed_terms,
        )
        extra = 1 if thread.tactic == "argue" else 0  # an argument deserves evidence
        return round(max(price, last), 2), settings.CLAIMS_LATER_ROUNDS + extra

    @telemetry.traced("negotiation.engine.choose_arguments", result_view=telemetry.topics_view)
    def choose_arguments(self, thread, count, threads):
        """The next unused arguments for this supplier: in a multi-bidder case the cheapest live rival
        offer first (a new offer is a new argument every round), then its own arguments, strongest first."""
        rivals = []
        for other_id, events in threads.items():
            if other_id == thread.supplier_id or latest(events).get("status") in ENDED:
                continue
            offer_id, terms = standing(events)
            if offer_id and terms.get("unit_price") and thread.their_prices:
                price = self.price_at_plan(thread, terms)
                if price < thread.their_prices[-1]:
                    rivals.append((price, live_rival_offer(other_id, offer_id, terms["unit_price"])))
        pool = [arg for _, arg in sorted(rivals, key=lambda row: row[0])[:1]] + thread.evidence
        chosen, chosen_families = [], set()
        for arg in pool:
            keys = [(thread.supplier_id, ref) if arg.get("rival") else ref for ref in arg["refs"]]
            if any(key in self.cited for key in keys):
                continue
            families = argument_families(arg)
            if families & (thread.used_topics | chosen_families):
                continue
            chosen.append(arg)
            chosen_families.update(families)
            if len(chosen) >= count:
                break
        return chosen

    def forecast_file(self, thread):
        """When it asks for proof: the part's forecast rows as a CSV attachment (figures from the data,
        nothing the arena checks as a claim), sent once."""
        if "proof" not in thread.requests or "forecast" in thread.files_sent:
            return []
        rows = []
        for part in self.case.get("parts") or []:
            for ref in self.evidence_bank.ids(f"fc:{part.split(':')[-1]}@"):
                row = self.evidence_bank.fact(ref)
                if row and row.get("volume_per_year") is not None:
                    rows.append((ref.split("@")[-1], part, row["volume_per_year"], row.get("change_pct")))
        if not rows:
            return []
        thread.files_sent.add("forecast")
        lines = ["month,part,volume_per_year,change_pct"] + [",".join(str(x) for x in row) for row in rows]
        content = base64.b64encode("\n".join(lines).encode()).decode()
        return [{"name": "forecast.csv", "type": "csv", "content_b64": content}]

    @telemetry.traced("negotiation.engine.dispatch", result_view=telemetry.message_view)
    def dispatch(self, thread, sheets, arguments, events, counter):
        """Build the e-mail for `sheets` and `arguments` and record what to commit or undo."""
        first = not thread.our_prices
        prices = thread.their_prices
        moved = len(prices) > 1 and prices[-1] < prices[-2] - settings.SAME_PRICE_TOLERANCE
        price = sheets[0]["unit_price"]
        keys = [(thread.supplier_id, ref) if arg.get("rival") else ref for arg in arguments for ref in arg["refs"]]
        families = set().union(*(argument_families(arg) for arg in arguments)) if arguments else set()
        thread.in_flight = {
            "phase": thread.phase_before,
            "cited": keys,
            "families": families,
            "primary": dict(sheets[0]),
            "alternative_keys": self.alternative_keys(sheets),
            "adviser_inquiry": (
                adviser_priorities.question(self.case, sheets[0], thread.standard)
                if settings.SELLER_ADVISER_INQUIRY
                and len(thread.our_prices) == 1
                and not thread.adviser_inquiry_sent
                and thread.phase == ACTIVE
                and latest(events).get("status") not in FROZEN
                else None
            ),
        }
        statements = mailer.binding_statements(self.case, thread, sheets)
        attachments = self.forecast_file(thread)
        if thread.phase != ECHO:
            thread.our_prices.append(price)
            thread.gap_history.append(max(0.0, (counter - price) / counter) if counter else 0.0)
            thread.in_flight["appended"] = True
        warm = thread.profile.get("warm", True)
        tone = "warm" if warm else "friendly and businesslike"
        clause_traded = any(entry["event"] == "index_clause_trade" for entry in self.journal)
        facts = self.briefing_facts(thread, first, len(sheets) > 1 and thread.phase == ACTIVE)
        facts += mailer.speakable_facts(self.case, thread, arguments, moved, statements, attachments, clause_traded)
        prompt = mailer.paragraph_prompt(
            self.case,
            thread.profile,
            latest(events).get("body"),
            facts,
            tone,
            first and not thread.in_flight["adviser_inquiry"],
            [thread.prep_notes, thread.reader_notes],
            history=transcript.build(self.case, events),
        )
        paragraph = model.write_paragraph(self.complete, prompt, self.write_budget_s, supplier_id=thread.supplier_id)
        body = mailer.compose_email(
            self.case,
            sheets,
            arguments,
            statements,
            first=first,
            moved=moved,
            warm=warm,
            paragraph=paragraph,
            ask_advisers=thread.in_flight["adviser_inquiry"],
        )
        self.log_event(
            thread.supplier_id,
            "send",
            phase=thread.phase,
            counter=counter,
            ours=price,
            arguments=[arg["topic"] for arg in arguments],
            requests=thread.requests,
            attached=[a["name"] for a in attachments],
        )
        message = {
            "subject": mailer.reply_subject(events, self.case),
            "body": body,
            "term_sheets": [{"terms": sheet} for sheet in sheets],
            "refs": sorted({ref for arg in arguments for ref in arg["refs"]}),
        }
        if attachments:
            message["attachments"] = attachments
        return {"message": message}

    @telemetry.traced("negotiation.engine.safe_message")
    def safe_message(self, supplier_id, threads):
        """A template acknowledgement after a failed move; it never resends an unchanged binding offer."""
        thread = self.suppliers.get(supplier_id)
        statements = []
        if thread and thread.last_primary_sent:
            statements = mailer.binding_statements(self.case, thread, [thread.last_primary_sent])
        self.log_event(supplier_id, "holding_reply", reason="template acknowledgement; no repeated sheet")
        return {
            "message": {
                "subject": mailer.reply_subject(threads[supplier_id], self.case),
                "body": mailer.holding_email(statements),
                "term_sheets": [],
                "refs": [],
            }
        }

    def confirm_delivery(self, supplier_id):
        """The message reached the supplier: its arguments are spent and its sheet is the last sent."""
        thread = self.suppliers[supplier_id]
        flight = thread.in_flight
        if flight:
            if flight.get("adviser_inquiry"):
                thread.adviser_inquiry_sent = True
                self.log_event(supplier_id, "adviser_inquiry_sent")
            self.cited.update(flight["cited"])
            thread.used_topics.update(flight.get("families", set()))
            thread.shown_alternatives.update(flight.get("alternative_keys", set()))
            if flight.get("primary"):
                thread.last_primary_sent = flight["primary"]
            thread.in_flight = None

    def undo_delivery(self, supplier_id):
        """The message arrived too late (a timeout): the supplier never read it."""
        thread = self.suppliers[supplier_id]
        flight = thread.in_flight
        if flight:
            if flight.get("appended"):
                thread.our_prices.pop()
                thread.gap_history.pop()
            thread.phase = flight["phase"]
            thread.in_flight = None
            thread.late_rounds += 1
            self.log_event(supplier_id, "undo_delivery", late_rounds=thread.late_rounds)

    def log_event(self, supplier_id, event, **details):
        entry = {"at": round(time.time(), 1), "supplier": supplier_id, "event": event, **details}
        self.journal.append(entry)
        if self.live_journal is not None:
            telemetry.append(self.live_journal, entry)  # guarded: a live view never costs the move
        log.info(
            "%s %s %s",
            self.case["case_id"],
            supplier_id,
            {k: v for k, v in entry.items() if k not in ("evidence", "standard", "profile")},
        )
