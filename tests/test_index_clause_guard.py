"""Tests for the buyer-cost guard in `PackageDesk.offer_index_clause` (T18)."""

import copy

import pytest
from arena_kit.sim import load_cases

from hackit2 import pricing, settings
from hackit2.negotiation import Negotiator, engine
from hackit2.supplier_profile import build_profile

RAW_CLAUSE = {"series": "raw", "material_share": 0.20, "deadband_pct": 5, "base_month": "2026-08"}


def _case_with_clause(category_goal="stabilise", path=0.05):
    case = copy.deepcopy(load_cases()["case:S01"])
    case["case_id"] = "case:unseen"
    case["suppliers"] = ["sup:unseen"]
    case["negotiable"].append("index_clause")
    case["fixed_terms"].pop("index_clause")
    case["term_options"]["index_clause"] = [None, RAW_CLAUSE]
    case["buyer"].update(
        category_goal=category_goal,
        sustainable_margin_pct=4,
        relationship_eur_per_point=2000,
        index_paths={"raw": path},
        index_risk_pct=4,
    )
    return case


def _open_supplier(case, negotiator, index_move, full_cost=None):
    terms = pricing.complete_offer(case, {"unit_price": 15, "payment_days": 30})
    event = {"from": "supplier", "status": "open", "body": "", "term_sheets": [{"terms": terms, "term_sheet_id": "q"}]}
    negotiator.evidence_bank.yearly_change = lambda series: index_move
    negotiator.supplier_full_cost = lambda part, supplier_id: (full_cost, full_cost is not None)
    profile = build_profile(None, "sup:unseen")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(engine, "build_profile", lambda pkg, supplier_id: dict(profile))
        return negotiator.open_thread("sup:unseen", [event])


def test_relationship_floor_clamping_clause_price_skips_index_clause():
    """When the relationship sweet-spot floor prevents the clause from discounting our opening price
    enough to cover index_risk (like P02), offer_index_clause skips the clause and logs index_clause_skipped."""
    case = _case_with_clause(category_goal="stabilise", path=0.02)
    negotiator = Negotiator(case)
    # Near-cost supplier (full_cost=14.9 vs quote=15.0): sweet_spot clamps the opening price,
    # so adding RAW_CLAUSE yields zero price reduction while adding index_risk > 0.
    thread = _open_supplier(case, negotiator, index_move=0.25, full_cost=14.9)

    assert not thread.plan.get("index_clause")
    skipped = [entry for entry in negotiator.journal if entry["event"] == "index_clause_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["c_with"] >= skipped[0]["c_without"]
    assert not any(entry["event"] == "index_clause_trade" for entry in negotiator.journal)


def test_leverage_case_adopts_index_clause_when_it_lowers_buyer_cost():
    """When there is no relationship floor clamping the discount and the clause lowers buyer C by
    more than the minimum margin, the index clause is still adopted."""
    case = _case_with_clause(category_goal="leverage", path=0.02)
    negotiator = Negotiator(case)
    thread = _open_supplier(case, negotiator, index_move=0.40, full_cost=12.0)

    assert thread.plan.get("index_clause") == RAW_CLAUSE
    traded = [entry for entry in negotiator.journal if entry["event"] == "index_clause_trade"]
    assert len(traded) == 1
    assert not any(entry["event"] == "index_clause_skipped" for entry in negotiator.journal)

    # Verify buyer C with the clause is strictly below buyer C without it at the unclamped pre-clause opening
    no_clause_plan = {**thread.plan, "index_clause": None}
    worth = pricing.supplier_worth(case, thread.plan, thread.valuation)
    pre_clause_price = round(pricing.price_for_worth(case, no_clause_plan, thread.valuation, worth), 2)
    c_without = pricing.buyer_cost(
        case, pricing.priced(no_clause_plan, pre_clause_price), thread.standard, thread.valuation
    )
    c_with = pricing.buyer_cost(case, thread.plan, thread.standard, thread.valuation)
    margin = max(settings.CHEAPER_PACKAGE_MIN_EUR, settings.CHEAPER_PACKAGE_MIN_SHARE * c_without)
    assert c_with < c_without - margin
