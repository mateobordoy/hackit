"""Displayed C uses official full-package costs, never unit price times a guessed quantity."""

import json

from conftest import s01_with_quote, supplier_quote

from hackit2 import pricing, session, telemetry
from hackit2.negotiation import Negotiator


def test_cost_components_sum_to_the_same_cost_as_the_engine():
    case, terms = s01_with_quote()
    summary = pricing.offer_summary(case, terms)
    assert summary["total_cost"] == pricing.buyer_cost(case, terms)
    assert round(sum(summary["breakdown"].values()), 2) == summary["total_cost"]
    changed = {**terms, "payment_days": terms["payment_days"] - 15}
    assert pricing.offer_summary(case, changed)["total_cost"] > summary["total_cost"]


def test_telemetry_prices_exact_historical_packages_and_full_cost_sweet_spot(tmp_path):
    case, terms = s01_with_quote()
    negotiator = Negotiator(case)
    sid = case["suppliers"][0]
    events = supplier_quote("open", terms)
    thread = negotiator.open_thread(sid, events)
    thread.sweet_spot = thread.opening_price
    old = {**terms, "payment_days": 30}
    events.append({"from": "buyer", "term_sheets": [{"terms": old}]})
    path = tmp_path / "costs.jsonl"
    token = telemetry.bind(path)
    try:
        session.publish_state(negotiator, sid, events)
        session.publish_message(sid, events[0], negotiator)
    finally:
        telemetry.unbind(token)
    state, message = [json.loads(line) for line in path.read_text().splitlines()]
    assert state["our_costs"] == [pricing.buyer_cost(case, old, thread.standard, thread.valuation)]
    expected = pricing.buyer_cost(
        case, pricing.priced(thread.plan, thread.sweet_spot), thread.standard, thread.valuation
    )
    assert state["sweet_spot_cost"] == expected
    assert len(state["cost_curve"]) == 31
    assert message["sheet_costs"][0]["total_cost"] == state["their_costs"][0]


def test_unknown_package_cost_is_explicitly_unavailable():
    case, _ = s01_with_quote()
    assert pricing.offer_summary(case, {})["total_cost"] is None
