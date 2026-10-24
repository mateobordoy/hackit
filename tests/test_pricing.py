"""Pure pricing arithmetic: the term planner and the supplier's worth of an index clause."""

import copy

from arena_kit import complete_offer
from arena_kit.sim import load_cases

from hackit2 import pricing, settings

RAW_CLAUSE = {"series": "raw", "material_share": 0.55, "deadband_pct": 5, "base_month": "2026-08"}


def test_cheap_price_makes_the_planner_cover_the_whole_need():
    """A 12-month contract leaves half of a two-year need to the fallback; at a low price the planner
    picks the contract that covers all of it."""
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["horizon_years"] = 2
    case["negotiable"].append("contract_months")
    case["fixed_terms"].pop("contract_months")
    case["term_options"]["contract_months"] = [12, 24]
    valuation = pricing.build_valuation(case, {}, 0.07)
    quote = complete_offer(case, {"unit_price": case["buyer"]["current_unit_price"], "contract_months": 12})
    terms, cost = pricing.cheapest_terms(case, {}, valuation, quote, quote["unit_price"] * 0.8)
    assert terms["contract_months"] == 24
    assert cost < pricing.buyer_cost(case, {**quote, "unit_price": terms["unit_price"]}, {}, valuation)


def test_index_clause_worth_is_capped_and_needs_a_known_trend():
    steep = {"index_trend": {"raw": 0.48}}
    assert pricing.clause_worth_share(RAW_CLAUSE, steep) == settings.INDEX_CLAUSE_VALUE_CAP
    assert (
        pricing.clause_worth({"index_clause": RAW_CLAUSE, "unit_price": 100}, steep)
        == 100 * settings.INDEX_CLAUSE_VALUE_CAP
    )
    assert pricing.clause_worth_share(RAW_CLAUSE, {"index_trend": {"raw": 0.10}}) == 0.55 * 0.05
    assert pricing.clause_worth_share(RAW_CLAUSE, {"index_trend": {}}) is None
