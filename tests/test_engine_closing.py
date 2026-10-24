"""The end of a case: the acceptance gate on the final decision, and retiring bidders that can no
longer beat a frozen (agreed or final) sheet. Scripted threads, no network, no model."""

import pytest
from arena_kit import complete_offer
from conftest import s01_with_quote, supplier_offer

from hackit2 import pricing
from hackit2.negotiation import ACTIVE, SETTLED, Negotiator, SupplierThread


def chosen_action(decision):
    return decision["message"]["action"]


def test_final_decision_walks_when_the_fallback_is_cheaper():
    case, terms = s01_with_quote(15.0)
    case["buyer"]["alternative"] = {
        "kind": "rfq",
        "terms": complete_offer(case, {"unit_price": 14.0, "payment_days": 60}),
    }
    negotiator = Negotiator(case)
    assert chosen_action(negotiator.final_decision({"sup:a": [supplier_offer("ts1", terms)]})) == {"type": "walk_away"}
    cheaper = {**terms, "unit_price": 13.0}
    assert chosen_action(negotiator.final_decision({"sup:a": [supplier_offer("ts2", cheaper)]})) == {
        "type": "accept",
        "term_sheet_id": "ts2",
    }


def test_relationship_charge_turns_an_accept_into_a_walk_away():
    case, terms = s01_with_quote(15.0)
    buyer = case["buyer"]
    buyer.update(category_goal="stabilise", relationship_eur_per_point=2000, sustainable_margin_pct=15)
    buyer["alternative"] = {"kind": "rfq", "terms": complete_offer(case, {"unit_price": 15.2, "payment_days": 60})}
    negotiator = Negotiator(case)
    threads = {"sup:a": [supplier_offer("ts1", terms)]}
    assert chosen_action(negotiator.final_decision(threads))["type"] == "accept"  # no full-cost estimate, no charge
    negotiator.suppliers["sup:a"] = thread = SupplierThread("sup:a")
    thread.valuation = pricing.build_valuation(case, {}, 0.08, full_cost=14.9)  # a margin far below 15 %
    assert pricing.acceptable_cost(case, terms, {}) is not None
    assert pricing.acceptable_cost(case, terms, {}, thread.valuation) is None
    assert chosen_action(negotiator.final_decision(threads)) == {"type": "walk_away"}


def test_malformed_sheets_are_skipped_without_failing_the_close():
    case, terms = s01_with_quote(15.0)
    negotiator = Negotiator(case)
    threads = {
        "sup:a": [supplier_offer("bad1", {"unit_price": None})],
        "sup:b": [supplier_offer("bad2", {**terms, "unit_price": "n/a"})],
        "sup:c": [supplier_offer("good", terms)],
    }
    assert chosen_action(negotiator.final_decision(threads)) == {"type": "accept", "term_sheet_id": "good"}


# --- retiring outclassed bidders ---------------------------------------------------------------


def priced_reply(terms, price, status="open", sheet_id="ts"):
    sheet = {"term_sheet_id": sheet_id, "terms": {**terms, "unit_price": price}}
    return {"from": "supplier", "status": status, "body": "Our offer.", "term_sheets": [sheet]}


def our_message(terms, *prices):
    return {
        "from": "buyer",
        "body": "Our proposal.",
        "term_sheets": [{"terms": {**terms, "unit_price": price}} for price in prices],
    }


def two_bidders(terms, status_of_a, prices_sent_to_b):
    """Bidder a ends at 14.0 with `status_of_a`; bidder b counters 15.5 after our sheets to it."""
    return {
        "sup:a": [
            priced_reply(terms, 16.0, sheet_id="a0"),
            our_message(terms, 14.0),
            priced_reply(terms, 14.0, status_of_a, "a1"),
        ],
        "sup:b": [
            priced_reply(terms, 16.5, sheet_id="b0"),
            our_message(terms, *prices_sent_to_b),
            priced_reply(terms, 15.5, sheet_id="b1"),
        ],
    }


def negotiator_with_active_threads(case, threads):
    negotiator = Negotiator(case)
    for supplier_id, events in threads.items():
        negotiator.open_thread(supplier_id, events[:1]).phase = ACTIVE
    return negotiator


@pytest.mark.parametrize("status", ["agreed", "final_offer"])
def test_bidder_unable_to_beat_a_frozen_sheet_is_retired(status):
    case, terms = s01_with_quote(16.0)
    threads = two_bidders(terms, status, [14.5])
    negotiator = negotiator_with_active_threads(case, threads)
    negotiator.retire_outclassed(threads)
    assert negotiator.suppliers["sup:b"].phase == SETTLED
    assert negotiator.suppliers["sup:a"].phase == ACTIVE  # the frozen thread itself goes through wrap_up
    assert [entry["supplier"] for entry in negotiator.journal if entry["event"] == "outclassed"] == ["sup:b"]


def test_our_cheaper_alternative_sheet_keeps_the_bidder_in_play():
    case, terms = s01_with_quote(16.0)
    threads = two_bidders(terms, "agreed", [14.5, 13.5])  # one of our sheets sits below the frozen deal
    negotiator = negotiator_with_active_threads(case, threads)
    negotiator.retire_outclassed(threads)
    assert negotiator.suppliers["sup:b"].phase == ACTIVE


def test_nothing_is_retired_without_an_acceptable_frozen_sheet(monkeypatch):
    case, terms = s01_with_quote(16.0)
    threads = two_bidders(terms, "open", [14.5])
    negotiator = negotiator_with_active_threads(case, threads)
    negotiator.retire_outclassed(threads)
    assert negotiator.suppliers["sup:b"].phase == ACTIVE
    threads = two_bidders(terms, "agreed", [14.5])
    negotiator = negotiator_with_active_threads(case, threads)
    monkeypatch.setattr(pricing, "acceptable_cost", lambda *args: None)  # dearer than the fallback
    negotiator.retire_outclassed(threads)
    assert negotiator.suppliers["sup:b"].phase == ACTIVE
