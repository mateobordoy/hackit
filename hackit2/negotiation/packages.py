"""Package handling for the negotiator: alternative sheets next to our main one, index-clause trades,
switching to a cheaper package the supplier quoted, and the guards against repeating ourselves.

`PackageDesk` is a mixin of `engine.Negotiator`; it relies on `self.case`, `self.evidence_bank`,
`self.price_at_plan` and `self.log_event`."""

import json
import re

from .. import adviser_priorities, pricing, settings, telemetry

# Why we changed a term, as the e-mail writer may explain it
NEED_REASONS = {
    "payment_days": "your request for earlier payment",
    "volume_per_year": "your request for a firm volume commitment",
    "lead_time_days": "your capacity constraint",
    "contract_months": "your capacity constraint and the value of planning security",
}


def same_offer(previous, candidate):
    """True when both sheets carry the same non-price terms and prices within half a cent."""
    if not previous or not candidate:
        return False
    if {k: v for k, v in previous.items() if k != "unit_price"} != {
        k: v for k, v in candidate.items() if k != "unit_price"
    }:
        return False
    try:
        gap = abs(float(previous.get("unit_price")) - float(candidate.get("unit_price")))
    except (TypeError, ValueError):
        return False
    return gap <= settings.SAME_PRICE_TOLERANCE


def counters_stalled(events):
    """True after three delivered supplier replies with identical complete packages (not merely the
    same price). Any new alternative, a missing sheet or a non-supplier event breaks the run. Offer
    IDs change on every reply, so only terms are compared; nothing is inferred from text."""
    replies = [e for e in events if e.get("from") != "buyer"][-3:]
    if len(replies) < 3 or any(e.get("from") != "supplier" for e in replies):
        return False
    packages = []
    for reply in replies:
        sheets = reply.get("term_sheets") or []
        if not sheets or any(not sheet.get("terms", {}).get("unit_price") for sheet in sheets):
            return False
        packages.append(sorted(json.dumps(sheet["terms"], sort_keys=True) for sheet in sheets))
    return packages[0] == packages[1] == packages[2]


def argument_families(arg):
    """Forecast rows for the same month make one demand-growth argument, whatever their IDs."""
    if arg.get("topic") != "forecast" or arg.get("rival"):
        return set()
    months = {month for ref in arg.get("refs", []) for month in re.findall(r"@(\d{4}-\d{2})", ref)}
    return {("forecast", month) for month in months}


def alternative_key(field, value):
    return json.dumps([field, value], sort_keys=True, separators=(",", ":"))


class PackageDesk:
    def alternative_keys(self, sheets):
        """Keys of the one-term alternatives among `sheets` (all but the first), so they are never
        offered twice, including after a resumed session."""
        if len(sheets) < 2:
            return set()
        main = sheets[0]
        keys = set()
        for sheet in sheets[1:]:
            changed = [f for f in self.case.get("negotiable", []) if f != "unit_price" and sheet.get(f) != main.get(f)]
            if len(changed) == 1:
                keys.add(alternative_key(changed[0], sheet[changed[0]]))
        return keys

    @telemetry.traced("negotiation.packages.prepared_alternatives", result_view=telemetry.prices_view)
    def prepared_alternatives(self, thread, main):
        """The lead researcher's trades as extra sheets next to our opening sheet `main`. Each changes
        one term and is priced so it costs us exactly what `main` costs: whichever the supplier picks,
        we lose nothing and learn what it values. A trade priced at or above what the supplier quotes
        for those terms, or far below it, is left out."""
        case, sheets = self.case, []
        main_cost = pricing.buyer_cost(case, main, thread.standard, thread.valuation)
        for trade in thread.prep_trades:
            terms = {**main, trade["field"]: trade["value"]}
            if terms == main or not pricing.must_haves_ok(case, terms, thread.standard):
                continue

            def cost_at(price, terms=terms):
                return pricing.buyer_cost(case, pricing.priced(terms, price), thread.standard, thread.valuation)

            low, high = 0.0, main["unit_price"] * 3
            if not cost_at(low) < main_cost < cost_at(high):
                continue
            for _ in range(50):  # C rises with the price
                mid = (low + high) / 2
                if cost_at(mid) < main_cost:
                    low = mid
                else:
                    high = mid
            sheet = pricing.priced(terms, int(low * 100) / 100)
            quoted_worth = pricing.supplier_worth(
                case, pricing.priced(thread.plan, thread.quote_at_plan), thread.valuation
            )
            quoted = pricing.price_for_worth(case, terms, thread.valuation, quoted_worth)
            if (
                not settings.TRADE_PRICE_MIN_SHARE * quoted
                <= sheet["unit_price"]
                < settings.TRADE_PRICE_MAX_SHARE * quoted
            ):
                continue
            sheets.append(sheet)
            self.log_event(
                thread.supplier_id,
                "prepared_alternative",
                trade=trade,
                price=sheet["unit_price"],
                quoted=round(quoted, 2),
            )
        return sheets

    @telemetry.traced("negotiation.packages.offer_index_clause")
    def offer_index_clause(self, thread, quote, full_cost):
        """Put an index clause into our plan when the supplier values it more than it costs us (its
        driver rose over the year while the case's path is small). It is priced to be worth the same
        to the supplier as our opening without it and, in a relationship goal, no lower than the
        relationship sweet spot. No trade when its value merely equals our cost."""
        case = self.case
        if "index_clause" not in case["negotiable"] or quote.get("index_clause"):
            return
        if "hedged" in thread.profile["traits"]:
            return  # material bought forward at fixed prices: the index does not reach its costs
        trend = thread.valuation["index_trend"]
        for clause in case.get("term_options", {}).get("index_clause") or []:
            if clause and clause.get("series") not in trend:
                change = self.evidence_bank.yearly_change(clause["series"])
                if change is not None:
                    trend[clause["series"]] = change
        clause = pricing.best_index_clause(case, thread.valuation)
        if not clause or thread.plan.get("index_clause") == clause:
            return
        worth = pricing.supplier_worth(case, thread.plan, thread.valuation)  # our opening as the supplier values it
        terms = {**thread.plan, "index_clause": clause}
        price = pricing.price_for_worth(case, terms, thread.valuation, worth)
        floor = None
        if full_cost and case["buyer"].get("category_goal") in pricing.RELATIONSHIP_GOALS:
            floor = pricing.relationship_sweet_spot(
                case,
                terms,
                thread.standard,
                thread.valuation,
                full_cost * settings.SWEET_SPOT_FLOOR_SHARE,
                thread.quote_at_plan,
            )
            price = max(price, floor or 0.0)
        if not pricing.must_haves_ok(case, pricing.priced(terms, price), thread.standard):
            return
        without_price = max(thread.plan["unit_price"], floor or 0.0)
        c_without = pricing.buyer_cost(
            case, pricing.priced(thread.plan, without_price), thread.standard, thread.valuation
        )
        c_with = pricing.buyer_cost(case, pricing.priced(terms, price), thread.standard, thread.valuation)
        margin = max(settings.CHEAPER_PACKAGE_MIN_EUR, settings.CHEAPER_PACKAGE_MIN_SHARE * c_without)
        if not c_with < c_without - margin:
            self.log_event(
                thread.supplier_id,
                "index_clause_skipped",
                clause=clause,
                c_with=round(c_with, 2),
                c_without=round(c_without, 2),
                price=round(price, 2),
            )
            return
        thread.plan = pricing.priced(terms, price)
        thread.opening_price = thread.plan["unit_price"]
        thread.sweet_spot = floor or thread.sweet_spot
        self.log_event(
            thread.supplier_id,
            "index_clause_trade",
            clause=clause,
            opening=thread.opening_price,
            sweet_spot=thread.sweet_spot,
        )

    @telemetry.traced("negotiation.packages.replan")
    def replan(self, thread, new_terms, why):
        """Switch to new non-price terms; our last price and its last price are converted so they keep
        the same worth to the supplier."""
        case = self.case
        if thread.our_prices:
            worth = pricing.supplier_worth(case, pricing.priced(thread.plan, thread.our_prices[-1]), thread.valuation)
            thread.our_prices[-1] = round(pricing.price_for_worth(case, new_terms, thread.valuation, worth), 2)
        if thread.their_prices:
            worth = pricing.supplier_worth(case, pricing.priced(thread.plan, thread.their_prices[-1]), thread.valuation)
            thread.their_prices[-1] = round(pricing.price_for_worth(case, new_terms, thread.valuation, worth), 2)
        thread.plan = {**new_terms}
        self.log_event(
            thread.supplier_id, "replan", why=why, terms={k: v for k, v in new_terms.items() if k != "unit_price"}
        )

    @telemetry.traced("negotiation.packages.adopt_cheaper_package")
    def adopt_cheaper_package(self, thread, sheets):
        """Compare the complete packages the supplier quoted and switch once to the cheapest one that
        clearly beats our plan at its current price."""
        if not thread.our_prices:
            return
        case = self.case
        standing = sheets[0]["terms"]
        planned_cost = pricing.buyer_cost(
            case, pricing.priced(thread.plan, self.price_at_plan(thread, standing)), thread.standard, thread.valuation
        )
        margin = max(settings.CHEAPER_PACKAGE_MIN_EUR, settings.CHEAPER_PACKAGE_MIN_SHARE * planned_cost)
        candidates = []
        for sheet in sheets:
            package = pricing.exact_package(case, sheet["terms"], thread.standard)
            if package is None or all(package.get(k) == v for k, v in thread.plan.items() if k != "unit_price"):
                continue
            c = pricing.acceptable_cost(case, package, thread.standard, thread.valuation)
            if c is not None and c < planned_cost - margin:
                candidates.append((c, package))
        if candidates:
            _, package = min(candidates, key=lambda row: (row[0], repr(sorted(row[1].items()))))
            self.replan(thread, package, "complete quoted package costs us less")

    @telemetry.traced("negotiation.packages.need_alternatives", result_view=telemetry.prices_view)
    def need_alternatives(self, thread, main):
        """Alternatives that answer a stated or evidenced supplier need, offered only when the complete
        package costs us less. Each keeps the supplier's estimated worth of `main`; must-haves, allowed
        values, the alternative and full buyer cost remain hard gates. Unknown value of volume or
        capacity is never invented."""
        traits = set(thread.profile.get("traits") or [])
        wanted = set()
        if "payment" in thread.requests or "cash_need" in traits:
            wanted.add(("payment_days", "lower"))
        if "volume" in thread.requests or "spare_capacity" in traits:
            wanted.add(("volume_per_year", "higher"))
        if "capacity_full" in traits:
            wanted.update({("lead_time_days", "higher"), ("contract_months", "higher")})
        if settings.SELLER_ADVISER_INQUIRY:
            wanted.update(adviser_priorities.wanted_trades(thread.adviser_priorities))
        if not wanted:
            thread.need_trades = []
            return []

        case = self.case
        base_cost = pricing.buyer_cost(case, main, thread.standard, thread.valuation)
        base_worth = pricing.supplier_worth(case, main, thread.valuation)
        found = []
        for field, direction in wanted:
            if field not in case.get("negotiable", []):
                continue
            current = main.get(field)
            eligible = []
            for value in (case.get("term_options") or {}).get(field) or []:
                if direction == "lower" and (current is None or value >= current):
                    continue
                if direction == "higher" and (current is None or value <= current):
                    continue
                if field == "volume_per_year" and value > case["buyer"].get("need_per_year", value):
                    continue  # never promise surplus purchases as a persuasion tactic
                if alternative_key(field, value) in thread.shown_alternatives:
                    continue
                terms = {**main, field: value}
                terms["unit_price"] = round(pricing.price_for_worth(case, terms, thread.valuation, base_worth), 2)
                terms = pricing.exact_package(case, terms, thread.standard)
                if terms is None or not pricing.must_haves_ok(case, terms, thread.standard):
                    continue
                c = pricing.acceptable_cost(case, terms, thread.standard, thread.valuation)
                if c is None or c >= base_cost - 1.0:
                    continue
                eligible.append((c, terms, field, value))
            if eligible:
                found.append(min(eligible, key=lambda row: (row[0], repr(sorted(row[1].items())))))
        found.sort(key=lambda row: (row[0], row[2], repr(row[3])))
        chosen = found[: settings.NEED_TRADES_MAX]
        thread.need_trades = [
            {"field": field, "value": value, "why": NEED_REASONS.get(field, "your stated need")}
            for _, _, field, value in chosen
        ]
        for c, _, field, value in chosen:
            self.log_event(
                thread.supplier_id,
                "need_alternative",
                field=field,
                value=value,
                buyer_cost=round(c, 2),
                main_cost=round(base_cost, 2),
                supplier_worth=round(base_worth, 2),
            )
        return [row[1] for row in chosen]
