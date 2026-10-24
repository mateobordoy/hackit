"""The relationship sweet spot: a supplier's price concession must not redefine our economic optimum,
only changed economics refresh it, and a counter below our full-cost estimate tightens the estimate."""

import copy

import pytest
from arena_kit.sim import load_cases

from hackit2 import pricing
from hackit2.negotiation import ACTIVE, SETTLED, Negotiator


def thread_in_progress():
    """An unknown supplier on S01 that quoted 15; we plan to open at 14 with a sweet spot of 14.8."""
    case = copy.deepcopy(load_cases()["case:S01"])
    case["case_id"] = "case:unseen-target"
    terms = pricing.complete_offer(case, {"unit_price": 15, "payment_days": 60})
    event = {
        "from": "supplier",
        "status": "open",
        "body": "Our quote.",
        "term_sheets": [{"term_sheet_id": "quoted", "terms": terms}],
    }
    negotiator = Negotiator(case)
    thread = negotiator.open_thread("sup:unseen", [event])
    thread.plan = dict(terms)
    thread.opening_price = 14
    thread.sweet_spot = 14.8
    thread.quote_at_plan = 15
    thread.valuation["full_cost"] = 14
    return negotiator, thread, event


def after_one_round(thread, counter_price, event):
    """We sent 14 against its 15; its new (unseen) reply counters at `counter_price`."""
    thread.our_prices = [14]
    thread.their_prices = [15]
    thread.last_seen_reply = object()
    event["term_sheets"][0]["terms"]["unit_price"] = counter_price


def test_opening_quote_keeps_the_planned_opening_and_sweet_spot(monkeypatch):
    negotiator, thread, event = thread_in_progress()
    thread.sweet_spot = 16

    def must_not_recompute(*args):
        raise AssertionError("unchanged opening economics must keep the sweet spot from setup")

    monkeypatch.setattr(pricing, "relationship_sweet_spot", must_not_recompute)
    decision = negotiator.next_message(thread.supplier_id, {thread.supplier_id: [event]})
    assert decision["message"]["term_sheets"][0]["terms"]["unit_price"] == 14
    assert thread.phase == ACTIVE
    assert thread.sweet_spot == 16


def test_price_only_counter_leaves_the_sweet_spot_alone(monkeypatch):
    negotiator, thread, event = thread_in_progress()
    after_one_round(thread, 14.82, event)
    monkeypatch.setattr(pricing, "relationship_sweet_spot", lambda *args: (_ for _ in ()).throw(AssertionError()))
    negotiator.next_message(thread.supplier_id, {thread.supplier_id: [event]})
    assert thread.sweet_spot == 14.8
    assert thread.phase == ACTIVE  # being 0.2 % away is no economic reason to stop
    assert thread.our_prices[-1] < 14.82


@pytest.mark.parametrize("counter_price", [14.8, 14.7])
def test_counter_at_or_below_the_sweet_spot_settles_on_its_standing_sheet(counter_price):
    negotiator, thread, event = thread_in_progress()
    after_one_round(thread, counter_price, event)
    threads = {thread.supplier_id: [event]}

    assert negotiator.next_message(thread.supplier_id, threads) is None
    assert thread.phase == SETTLED
    assert thread.our_prices == [14]  # no new sheet of ours above its counter
    assert negotiator.final_decision(threads)["message"]["action"] == {"type": "accept", "term_sheet_id": "quoted"}


def test_changed_economics_recompute_the_sweet_spot_over_an_unclipped_range(monkeypatch):
    negotiator, thread, event = thread_in_progress()
    after_one_round(thread, 14.7, event)
    thread.sweet_spot = 16

    def money_got_dearer(target_thread, body):
        target_thread.valuation["interest_rate"] += 0.01

    negotiator.absorb_traits = money_got_dearer
    search_ranges = []

    def fake_sweet_spot(case, terms, standard, valuation, low, high):
        search_ranges.append((low, high))
        return 16

    monkeypatch.setattr(pricing, "relationship_sweet_spot", fake_sweet_spot)
    assert negotiator.next_message(thread.supplier_id, {thread.supplier_id: [event]}) is None
    assert len(search_ranges) == 1
    assert search_ranges[0][1] >= 16
    assert thread.sweet_spot == 16
    assert thread.our_prices == [14]
    assert thread.phase == SETTLED


def test_counter_below_our_cost_estimate_caps_it_and_moves_the_sweet_spot():
    """No supplier sells below its cost: its counter caps our full-cost estimate, the sweet spot follows,
    and we step toward the counter instead of settling above it."""
    negotiator, thread, event = thread_in_progress()
    negotiator.case["buyer"].update(
        category_goal="stabilise", sustainable_margin_pct=1, relationship_eur_per_point=2000
    )
    after_one_round(thread, 14.7, event)  # below the 14.8 sweet spot, which once settled the thread
    thread.valuation["full_cost"] = 14.9
    thread.sweet_spot = 14.8
    decision = negotiator.next_message(thread.supplier_id, {thread.supplier_id: [event]})
    assert thread.valuation["full_cost"] <= 14.7
    assert thread.sweet_spot < 14.7
    assert thread.phase == ACTIVE
    assert decision["message"]["term_sheets"][0]["terms"]["unit_price"] < 14.7


def test_counter_worth_more_to_the_supplier_than_our_estimate_leaves_it_unchanged(monkeypatch):
    """A clause the supplier values keeps a low price above its cost in its own valuation."""
    negotiator, thread, event = thread_in_progress()
    after_one_round(thread, 14.0, event)
    thread.valuation["full_cost"] = 14.9
    monkeypatch.setattr(pricing, "supplier_worth", lambda case, terms, valuation: terms["unit_price"] * 1.08)
    negotiator.next_message(thread.supplier_id, {thread.supplier_id: [event]})
    assert thread.valuation["full_cost"] == 14.9
