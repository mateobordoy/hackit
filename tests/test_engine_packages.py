"""Package handling in the negotiation engine: quoted packages are evaluated and sent back whole,
never snapped or silently altered; a price cut alone never buys worse terms; supplier-need
alternatives are offered only when they cost us less and break no must-have."""

import copy

import pytest
from arena_kit.sim import load_cases
from conftest import LEVERAGE_QUOTE, leverage_case, supplier_quote

from hackit2 import pricing
from hackit2.negotiation import ACTIVE, ECHO, SETTLED, Negotiator


def unseen_index_case():
    """An unknown supplier on S01 with an open index clause on a rising series; the thread has sent
    one sheet at 14 and plans the quoted terms."""
    case = copy.deepcopy(load_cases()["case:S01"])
    case["case_id"] = "case:unseen"
    case["suppliers"] = ["sup:unseen"]
    case["negotiable"].append("index_clause")
    case["fixed_terms"].pop("index_clause")
    rising = {"series": "raw", "material_share": 0.5, "deadband_pct": 0, "base_month": "2026-08"}
    protected = {**rising, "deadband_pct": 5}
    case["term_options"]["index_clause"] = [None, rising, protected]
    case["buyer"]["index_paths"] = {"raw": 0.06}
    terms = pricing.complete_offer(case, {"unit_price": 15, "payment_days": 30, "index_clause": rising})
    negotiator = Negotiator(case)
    event = {"from": "supplier", "status": "open", "body": "", "term_sheets": [{"terms": terms, "term_sheet_id": "q"}]}
    thread = negotiator.open_thread("sup:unseen", [event])
    thread.plan = terms.copy()
    thread.our_prices = [14]
    return case, negotiator, thread, terms, protected


@pytest.mark.parametrize("reverse", [False, True])
def test_dearer_price_with_lower_total_cost_is_adopted_as_a_whole(reverse):
    case, negotiator, thread, terms, protected = unseen_index_case()
    cheaper_overall = {**terms, "unit_price": 15.1, "index_clause": protected}
    dearer_overall = {**terms, "unit_price": 15.2, "index_clause": protected}
    assert pricing.buyer_cost(case, cheaper_overall) < pricing.buyer_cost(case, terms)
    alternatives = [cheaper_overall, dearer_overall]
    if reverse:
        alternatives.reverse()
    negotiator.adopt_cheaper_package(thread, [{"terms": sheet} for sheet in [terms, *alternatives]])
    assert thread.plan == cheaper_overall


def test_exact_package_never_snaps_terms_or_overrides_fixed_ones():
    case, _, thread, terms, _ = unseen_index_case()
    assert pricing.exact_package(case, terms, thread.standard) == terms
    assert pricing.exact_package(case, {**terms, "payment_days": 37}, thread.standard) is None
    fixed_field = next(iter(case["fixed_terms"]))
    assert pricing.exact_package(case, {**terms, fixed_field: "not the fixed value"}, thread.standard) is None
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 60}]
    assert pricing.exact_package(case, terms, thread.standard) is None


def test_wrap_up_echoes_the_exact_cheaper_package_and_ignores_invalid_ones():
    case, negotiator, thread, terms, protected = unseen_index_case()
    cheaper_overall = {**terms, "unit_price": 15.1, "index_clause": protected}
    final_words = [{"from": "supplier", "body": "Final quote"}]
    decision = negotiator.wrap_up(thread, [{"terms": terms}, {"terms": cheaper_overall}], final_words, 15, 1)
    assert decision["message"]["term_sheets"] == [{"terms": cheaper_overall}]
    assert thread.phase == ECHO
    thread.phase = ACTIVE
    invalid = {**cheaper_overall, "payment_days": 37}
    assert negotiator.wrap_up(thread, [{"terms": terms}, {"terms": invalid}], final_words, 15, 1) is None
    assert thread.phase == SETTLED


def test_fixed_payment_term_is_stated_by_code_without_a_model():
    case, negotiator, thread, terms, _ = unseen_index_case()
    case["negotiable"].remove("payment_days")
    case["fixed_terms"]["payment_days"] = 30
    thread.requests = ["payment"]
    decision = negotiator.dispatch(thread, [terms], [], [{"from": "supplier", "body": "Can you pay faster?"}], 15)
    assert "Payment is fixed at 30 days" in decision["message"]["body"]
    assert "pay reliably" not in decision["message"]["body"]


def test_explicit_fixed_price_is_valid_whatever_the_option_order():
    case, _, thread, terms, protected = unseen_index_case()
    case["term_options"]["index_clause"] = [protected, None]
    fixed_price = {**terms, "index_clause": None}
    assert pricing.exact_package(case, fixed_price, thread.standard) == fixed_price


def test_small_price_cut_does_not_buy_a_worse_package():
    case, negotiator, thread, terms, _ = unseen_index_case()
    thread.plan["payment_days"] = 90
    counter = {**terms, "unit_price": 14.99, "payment_days": 30}
    plan_before = thread.plan.copy()
    negotiator.adopt_cheaper_package(thread, [{"terms": counter}])
    assert thread.plan == plan_before


def test_index_clause_is_only_taken_when_its_package_beats_our_plan():
    """An index clause on a rising series is adopted only if the sheet, clause included, costs less than
    the planned package, and never when it breaks a must-have."""
    case = copy.deepcopy(load_cases()["case:S01"])
    clause = {"series": "WB-CU", "material_share": 0.2, "deadband_pct": 0, "base_month": "2026-08"}
    case["negotiable"].append("index_clause")
    case["fixed_terms"].pop("index_clause")
    case["term_options"]["index_clause"] = [None, clause]
    case["buyer"]["index_paths"] = {"WB-CU": 0.05}
    quote = {"unit_price": 15.88, "payment_days": 30, "index_clause": None}

    def opened_thread(case):
        negotiator = Negotiator(case)
        thread = negotiator.open_thread("sup:S", supplier_quote("open", quote))
        assert thread.plan["index_clause"] is None
        thread.our_prices.append(thread.opening_price)
        return negotiator, thread

    negotiator, thread = opened_thread(case)
    token_cut = {**quote, "unit_price": 15.80, "index_clause": clause}
    negotiator.adopt_cheaper_package(thread, [{"term_sheet_id": "offer:S-2", "terms": token_cut}])
    assert thread.plan["index_clause"] is None  # a token cut does not pay for the clause
    deep_cut = {**quote, "unit_price": 14.0, "index_clause": clause}
    negotiator.adopt_cheaper_package(thread, [{"term_sheet_id": "offer:S-3", "terms": deep_cut}])
    assert thread.plan["index_clause"] is None  # nor does a price cut on its own justify worse terms

    case["buyer"]["must_haves"] = [{"field": "index_clause", "op": "==", "value": None}]
    negotiator, thread = opened_thread(case)
    negotiator.adopt_cheaper_package(thread, [{"term_sheet_id": "offer:S-4", "terms": deep_cut}])
    assert thread.plan["index_clause"] is None


def test_cash_need_only_earns_cost_checked_earlier_payment():
    case = leverage_case()
    negotiator = Negotiator(case)
    thread = negotiator.open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    thread.valuation["interest_rate"] = 0.5
    thread.requests = ["payment"]
    main = pricing.complete_offer(case, {"unit_price": LEVERAGE_QUOTE["unit_price"], "payment_days": 60})
    main_cost = pricing.buyer_cost(case, main, thread.standard, thread.valuation)
    alternatives = negotiator.need_alternatives(thread, main)
    assert alternatives
    assert all(sheet["payment_days"] < main["payment_days"] for sheet in alternatives)
    assert all(pricing.must_haves_ok(case, sheet, thread.standard) for sheet in alternatives)
    assert all(
        pricing.acceptable_cost(case, sheet, thread.standard, thread.valuation) is not None for sheet in alternatives
    )
    assert all(pricing.buyer_cost(case, sheet, thread.standard, thread.valuation) < main_cost for sheet in alternatives)


def test_volume_request_never_offers_more_than_our_need_and_respects_must_haves():
    case = leverage_case()
    need = case["buyer"]["need_per_year"]
    case["negotiable"].append("volume_per_year")
    case["fixed_terms"].pop("volume_per_year", None)
    case["term_options"]["volume_per_year"] = [need // 2, need, need * 2]
    negotiator = Negotiator(case)
    thread = negotiator.open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    thread.requests = ["volume"]
    main = pricing.complete_offer(
        case, {"unit_price": LEVERAGE_QUOTE["unit_price"], "payment_days": 60, "volume_per_year": need // 2}
    )
    main_cost = pricing.buyer_cost(case, main, thread.standard, thread.valuation)
    alternatives = negotiator.need_alternatives(thread, main)
    assert alternatives and all(sheet["volume_per_year"] == need for sheet in alternatives)
    assert all(pricing.buyer_cost(case, sheet, thread.standard, thread.valuation) < main_cost for sheet in alternatives)
    case["buyer"]["must_haves"] = [{"field": "volume_per_year", "op": "<=", "value": need // 2}]
    assert negotiator.need_alternatives(thread, main) == []
