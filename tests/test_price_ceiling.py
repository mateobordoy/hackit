"""The buyer's fallback as a hard price ceiling. A frozen offer permits acceptance or an exact
seller-package echo, never a newly invented proposal at that ceiling."""

import copy

import pytest
from arena_kit.cost import complete_offer
from arena_kit.sim import load_cases

from hackit2 import pricing
from hackit2.negotiation import ECHO, SETTLED, Negotiator


def s01_with_fallback(fallback_price):
    case = copy.deepcopy(load_cases()["case:S01"])
    case["case_id"] = "case:break-even"
    fallback_terms = complete_offer(case, {"unit_price": fallback_price, "payment_days": 60})
    case["buyer"]["alternative"] = {"kind": "quote", "terms": fallback_terms}
    return case


def counter(price, status="open"):
    sheet = {"term_sheet_id": f"o{price}", "terms": {"unit_price": price, "payment_days": 60}}
    return {"from": "supplier", "status": status, "body": "", "term_sheets": [sheet]}


def first_sheet(move):
    return move["message"]["term_sheets"][0]["terms"]


def test_ceiling_is_the_highest_price_that_still_beats_the_fallback():
    case = s01_with_fallback(16.0)
    terms = complete_offer(case, {"unit_price": 15.0, "payment_days": 60})
    ceiling = pricing.price_ceiling(case, terms, {})
    assert pricing.acceptable_cost(case, pricing.priced(terms, ceiling), {}) is not None
    assert pricing.acceptable_cost(case, pricing.priced(terms, ceiling + 0.02), {}) is None
    # the stock case has no fallback, so it has no ceiling either
    assert pricing.price_ceiling(load_cases()["case:S01"], terms, {}) is None


def test_opening_and_follow_up_stay_below_a_low_fallback():
    case = s01_with_fallback(14.0)  # cheaper than the usual leverage opening of 0.78 x 18
    negotiator = Negotiator(case)
    supplier_id = case["suppliers"][0]
    events = [counter(18.0)]
    negotiator.open_thread(supplier_id, events)
    opening = first_sheet(negotiator.next_message(supplier_id, {supplier_id: events}))
    assert pricing.acceptable_cost(case, opening, {}) is not None
    negotiator.confirm_delivery(supplier_id)
    events += [{"from": "buyer", "term_sheets": [{"terms": opening}]}, counter(17.0)]
    follow_up = negotiator.next_message(supplier_id, {supplier_id: events})
    assert follow_up is None or pricing.acceptable_cost(case, first_sheet(follow_up), {}) is not None


@pytest.mark.parametrize("status", ["final_offer", "agreed"])
def test_unacceptable_frozen_offer_never_gets_a_new_ceiling_proposal(status):
    case = s01_with_fallback(16.0)
    negotiator = Negotiator(case)
    supplier_id = case["suppliers"][0]
    events = [counter(18.0)]
    thread = negotiator.open_thread(supplier_id, events)
    opening = first_sheet(negotiator.next_message(supplier_id, {supplier_id: events}))
    negotiator.confirm_delivery(supplier_id)
    events += [{"from": "buyer", "term_sheets": [{"terms": opening}]}, counter(16.5, status)]
    last_try = negotiator.next_message(supplier_id, {supplier_id: events})
    assert last_try is None and thread.phase == SETTLED
    assert negotiator.final_decision({supplier_id: events})["message"]["action"]["type"] == "walk_away"


@pytest.mark.parametrize("status", ["final_offer", "agreed"])
def test_frozen_offer_still_allows_exact_echo_of_a_cheaper_supplier_alternative(status):
    case = s01_with_fallback(16.0)
    negotiator = Negotiator(case)
    supplier_id = case["suppliers"][0]
    events = [counter(18.0)]
    thread = negotiator.open_thread(supplier_id, events)
    opening = first_sheet(negotiator.next_message(supplier_id, {supplier_id: events}))
    negotiator.confirm_delivery(supplier_id)
    final = counter(16.5, status)
    alternative = {"unit_price": 14.0, "payment_days": 60}
    final["term_sheets"].append({"term_sheet_id": "cheaper", "terms": alternative})
    events += [{"from": "buyer", "term_sheets": [{"terms": opening}]}, final]
    move = negotiator.next_message(supplier_id, {supplier_id: events})
    assert move["message"]["term_sheets"] == [{"terms": alternative}]
    assert thread.phase == ECHO


def test_relationship_ceiling_is_searched_inside_the_feasible_range():
    case = s01_with_fallback(20.0)
    case["buyer"].update(category_goal="stabilise", relationship_eur_per_point=5000, sustainable_margin_pct=10)
    terms = complete_offer(case, {"unit_price": 15, "payment_days": 60})
    valuation = pricing.build_valuation(case, {}, 0.07, full_cost=14)
    assert pricing.buyer_cost(case, pricing.priced(terms, 0), {}, valuation) == float("inf")
    ceiling = pricing.price_ceiling(case, terms, {}, valuation)
    assert ceiling > 0
    assert pricing.acceptable_cost(case, pricing.priced(terms, ceiling), {}, valuation) is not None


def test_hopeless_fallback_still_opens_without_a_zero_price_and_then_walks():
    # nothing at our terms beats the fallback on our estimate: we still open (its counters may tighten
    # the estimate), never with a 0.0 sheet, and the close walks when nothing beats the fallback
    case = s01_with_fallback(0.01)
    negotiator = Negotiator(case)
    supplier_id = case["suppliers"][0]
    events = [counter(18)]
    negotiator.open_thread(supplier_id, events)
    move = negotiator.next_message(supplier_id, {supplier_id: events})
    assert move is not None
    assert all(sheet["terms"]["unit_price"] > 0 for sheet in move["message"]["term_sheets"])
    assert negotiator.final_decision({supplier_id: events})["message"]["action"]["type"] == "walk_away"


def test_overestimated_full_cost_does_not_end_the_thread_before_the_opening():
    # stabilise goal: a full-cost estimate above the true one makes every price look dearer than the
    # fallback (relationship charge); the supplier's lower counter caps the estimate and the ceiling
    # appears, which leaving before the first sheet would never have revealed
    case = s01_with_fallback(17.0)
    case["buyer"].update(category_goal="stabilise", relationship_eur_per_point=20_000, sustainable_margin_pct=8)
    supplier_id = case["suppliers"][0]
    negotiator = Negotiator(case)
    events = [counter(18.0)]
    thread = negotiator.open_thread(supplier_id, events)
    thread.valuation["full_cost"], thread.sweet_spot = 17.2, None
    assert pricing.price_ceiling(case, thread.plan, thread.standard, thread.valuation) == 0.0
    opening = negotiator.next_message(supplier_id, {supplier_id: events})
    assert opening is not None
    negotiator.confirm_delivery(supplier_id)
    events += [{"from": "buyer", "term_sheets": opening["message"]["term_sheets"]}, counter(15.5)]
    negotiator.next_message(supplier_id, {supplier_id: events})
    assert thread.valuation["full_cost"] < 17.2
    assert pricing.price_ceiling(case, thread.plan, thread.standard, thread.valuation) > 0


def test_relationship_charge_never_pushes_the_ceiling_down_to_zero():
    # under the supplier's full cost the relationship charge makes a lower price dearer; the ceiling
    # is the top of the price band that beats the fallback, not zero
    case = s01_with_fallback(16.0)
    case["buyer"].update(category_goal="secure_supply", relationship_eur_per_point=1_000, sustainable_margin_pct=5)
    supplier_id = case["suppliers"][0]
    thread = Negotiator(case).open_thread(supplier_id, [counter(18.0)])
    plan, standard = thread.plan, thread.standard
    valuation = {**thread.valuation, "full_cost": 14.0}
    assert pricing.buyer_cost(case, pricing.priced(plan, 0.01), standard, valuation) > pricing.fallback_cost(case)
    ceiling = pricing.price_ceiling(case, plan, standard, valuation)
    assert ceiling is not None and ceiling > 14.0
    assert pricing.acceptable_cost(case, pricing.priced(plan, ceiling), standard, valuation) is not None
    assert pricing.acceptable_cost(case, pricing.priced(plan, ceiling + 0.02), standard, valuation) is None
