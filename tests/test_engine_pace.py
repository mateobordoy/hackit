"""Pace and progress of a thread: what counts as a stalled supplier, when a stall settles the thread,
argument selection across rounds, the no-repeat guard, the reply reader's tactic and the template
reply after a failed move. No network, no model."""

import copy

import pytest
from arena_kit.sim import load_cases
from conftest import LEVERAGE_QUOTE, leverage_case, supplier_quote

from hackit2 import pricing
from hackit2.negotiation import ACTIVE, SETTLED, Negotiator, SupplierThread
from hackit2.negotiation.packages import argument_families, counters_stalled, same_offer


def counter_event(price=18, payment=60, sheet_id="a"):
    return {
        "from": "supplier",
        "term_sheets": [{"term_sheet_id": sheet_id, "terms": {"unit_price": price, "payment_days": payment}}],
    }


def test_new_payment_term_or_extra_alternative_breaks_a_stall_at_the_same_price():
    events = [counter_event(sheet_id="a"), counter_event(sheet_id="b"), counter_event(sheet_id="c")]
    assert counters_stalled(events)
    assert not counters_stalled(events[:2])
    assert not counters_stalled(events[:2] + [counter_event(payment=30)])
    with_alternative = copy.deepcopy(events[-1])
    with_alternative["term_sheets"].append({"terms": {"unit_price": 18, "payment_days": 90}})
    assert not counters_stalled(events[:2] + [with_alternative])
    assert not counters_stalled(events[:2] + [{"from": "arena", "term_sheets": []}])


@pytest.mark.parametrize(
    ("fresh_argument", "fresh_alternative", "expect_settled"),
    [(False, False, True), (True, False, False), (False, True, False)],
)
def test_stall_settles_only_when_nothing_new_can_be_offered(
    monkeypatch, fresh_argument, fresh_alternative, expect_settled
):
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["category_goal"] = "leverage"
    negotiator = Negotiator(case)
    supplier_id = case["suppliers"][0]
    events = [counter_event(sheet_id=str(i)) for i in range(3)]
    thread = negotiator.open_thread(supplier_id, events[:1])
    thread.our_prices = [12]
    thread.evidence = []
    thread.sweet_spot = None
    monkeypatch.setattr(
        negotiator, "choose_arguments", lambda *args: [{"refs": ["doc:independent"]}] if fresh_argument else []
    )
    monkeypatch.setattr(
        negotiator,
        "need_alternatives",
        lambda target_thread, main: [{**main, "payment_days": 30}] if fresh_alternative else [],
    )
    monkeypatch.setattr(negotiator, "dispatch", lambda *args: {"message": "continue"})
    move = negotiator.next_message(supplier_id, {supplier_id: events})
    assert (move is None) == expect_settled
    if expect_settled:
        assert thread.phase == SETTLED
        assert any(entry["event"] == "supplier_stalled" for entry in negotiator.journal)


def test_stall_never_settles_on_an_unacceptable_standing_offer(monkeypatch):
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["category_goal"] = "leverage"
    negotiator = Negotiator(case)
    supplier_id = case["suppliers"][0]
    events = [counter_event() for _ in range(3)]
    thread = negotiator.open_thread(supplier_id, events[:1])
    thread.our_prices = [12]
    thread.evidence = []
    monkeypatch.setattr(pricing, "acceptable_cost", lambda *args: None)
    monkeypatch.setattr(negotiator, "dispatch", lambda *args: {"message": "continue"})
    assert negotiator.next_message(supplier_id, {supplier_id: events}) is not None


def test_one_forecast_argument_per_month_then_fresh_evidence_only():
    negotiator = Negotiator(leverage_case())
    thread = negotiator.open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    arguments = [
        {"topic": "forecast", "refs": ["fc:360010@2026-09"], "sentence": "Forecast for part A", "weight": 3},
        {"topic": "forecast", "refs": ["fc:360011@2026-09"], "sentence": "Forecast for part B", "weight": 2},
        {"topic": "forecast", "refs": ["fc:360010@2026-10"], "sentence": "New month for part A", "weight": 2},
        {"topic": "should_cost", "refs": ["cost:part-a"], "sentence": "Cost evidence", "weight": 5},
    ]
    thread.evidence = arguments
    no_rivals = {thread.supplier_id: []}
    assert negotiator.choose_arguments(thread, 3, no_rivals) == [arguments[0], arguments[2], arguments[3]]
    thread.in_flight = {
        "phase": ACTIVE,
        "cited": arguments[0]["refs"],
        "families": argument_families(arguments[0]),
    }
    negotiator.confirm_delivery(thread.supplier_id)
    assert negotiator.choose_arguments(thread, 3, no_rivals) == [arguments[2], arguments[3]]


def test_same_offer_ignores_rounding_but_not_term_changes():
    previous = {"unit_price": 45.43, "payment_days": 60, "volume_per_year": 100}
    assert same_offer(previous, {**previous, "unit_price": 45.434})
    assert not same_offer(previous, {**previous, "unit_price": 45.45})
    assert not same_offer(previous, {**previous, "payment_days": 30})


def test_thread_settles_instead_of_resending_its_last_main_sheet():
    case = leverage_case()
    supplier_id = "sup:S"
    terms = pricing.complete_offer(case, {"unit_price": 15.0, "payment_days": 30})
    reply = {
        "from": "supplier",
        "status": "offer",
        "body": "Our offer.",
        "term_sheets": [{"terms": {**terms, "unit_price": 15.5}}],
    }
    negotiator = Negotiator(case)
    thread = SupplierThread(supplier_id)
    thread.plan = terms
    thread.our_prices = [15.0]
    thread.their_prices = [16.0, 15.5]
    thread.last_seen_reply = reply
    next_price = negotiator.next_offer_price(thread, 15.5)[0]
    thread.last_primary_sent = pricing.priced(terms, next_price)
    negotiator.suppliers[supplier_id] = thread
    assert negotiator.next_message(supplier_id, {supplier_id: [reply]}) is None
    assert thread.phase == SETTLED
    assert negotiator.journal[-1]["event"] == "no_progress"


def test_holding_reply_answers_a_known_request_without_a_term_sheet():
    case = leverage_case()
    case["negotiable"].remove("payment_days")
    case["fixed_terms"]["payment_days"] = 30
    supplier_id = "sup:S"
    thread = SupplierThread(supplier_id)
    thread.standard = {}
    thread.requests = ["payment"]
    thread.last_primary_sent = pricing.complete_offer(case, {"unit_price": 15.0})
    negotiator = Negotiator(case)
    negotiator.suppliers[supplier_id] = thread
    threads = {supplier_id: [{"from": "supplier", "subject": "Re: offer", "body": "Can you pay sooner?"}]}
    reply = negotiator.safe_message(supplier_id, threads)
    assert reply["message"]["term_sheets"] == []
    assert "Payment is fixed at 30 days" in reply["message"]["body"]


def test_reader_tactic_slows_the_step_or_adds_an_argument():
    negotiator = Negotiator(leverage_case())
    thread = negotiator.open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    thread.our_prices, thread.their_prices, thread.gap_history = [12.0, 12.6], [15.88, 15.6, 15.5], [0.2, 0.15]
    steady_price, argument_count = negotiator.next_offer_price(thread, 15.5)
    thread.tactic = "hold"
    assert negotiator.next_offer_price(thread, 15.5)[0] < steady_price
    thread.tactic = "argue"
    assert negotiator.next_offer_price(thread, 15.5) == (steady_price, argument_count + 1)


def test_hold_does_not_slow_a_hedged_supplier_on_our_second_sheet():
    """A hedged supplier's first counter always reads firm ('covered at fixed prices'), and slowing our
    second step made it run out of patience a round early (P08: 0.538 vs 0.737)."""
    negotiator = Negotiator(leverage_case())
    thread = negotiator.open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    thread.profile["traits"] = ["hedged"]
    thread.our_prices, thread.their_prices, thread.gap_history = [12.0], [15.88, 15.5], [0.2]
    steady_price, _ = negotiator.next_offer_price(thread, 15.5)
    thread.tactic = "hold"
    assert negotiator.next_offer_price(thread, 15.5)[0] == steady_price
