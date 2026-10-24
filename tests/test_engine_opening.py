"""Opening a thread: the index-clause trade and the relationship sweet spot for a supplier the data
package does not know, no should-cost for a bundle, and a quote PDF with one unreadable line."""

import copy

import pytest
from arena_kit.docs import standard_terms
from arena_kit.sim import load_cases
from conftest import s01_with_quote, supplier_offer

from hackit2 import pricing, settings
from hackit2.negotiation import Negotiator, engine
from hackit2.negotiation.thread import quoted_standard_terms
from hackit2.supplier_profile import build_profile

RAW_CLAUSE = {"series": "raw", "material_share": 0.55, "deadband_pct": 5, "base_month": "2026-08"}


def stabilise_case_with_clause():
    case = copy.deepcopy(load_cases()["case:S01"])
    case["case_id"] = "case:unseen"
    case["suppliers"] = ["sup:unseen"]
    case["negotiable"].append("index_clause")
    case["fixed_terms"].pop("index_clause")
    case["term_options"]["index_clause"] = [None, RAW_CLAUSE]
    case["buyer"].update(
        category_goal="stabilise", sustainable_margin_pct=4, index_paths={"raw": 0.05}, index_risk_pct=4
    )
    return case


def open_unknown_supplier(case, negotiator, index_move, full_cost=None, traits=()):
    """Open the thread of an unknown supplier quoting 15, with a given yearly index move, should-cost
    and profile traits."""
    terms = pricing.complete_offer(case, {"unit_price": 15, "payment_days": 30})
    event = {"from": "supplier", "status": "open", "body": "", "term_sheets": [{"terms": terms, "term_sheet_id": "q"}]}
    negotiator.evidence_bank.yearly_change = lambda series: index_move
    negotiator.supplier_full_cost = lambda part, supplier_id: (full_cost, full_cost is not None)
    profile = build_profile(None, "sup:unseen")
    profile["traits"] = sorted(traits)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(engine, "build_profile", lambda pkg, supplier_id: dict(profile))
        return negotiator.open_thread("sup:unseen", [event])


def test_hedged_supplier_is_offered_no_index_clause():
    case = stabilise_case_with_clause()
    negotiator = Negotiator(case)
    thread = open_unknown_supplier(case, negotiator, index_move=0.48, traits=["hedged"])
    assert not thread.plan.get("index_clause")
    assert not any(entry["event"] == "index_clause_trade" for entry in negotiator.journal)


def test_extreme_index_move_cannot_lower_the_opening_beyond_the_clause_cap():
    case = stabilise_case_with_clause()
    thread = open_unknown_supplier(case, Negotiator(case), index_move=0.48)
    assert thread.plan.get("index_clause") == RAW_CLAUSE
    worth_without_clause = pricing.supplier_worth(
        case, {**thread.plan, "index_clause": None, "unit_price": thread.opening_price}, thread.valuation
    )
    worth_with_clause = pricing.supplier_worth(case, thread.plan, thread.valuation)
    assert worth_with_clause - worth_without_clause <= settings.INDEX_CLAUSE_VALUE_CAP * thread.opening_price + 1e-6


def test_supplier_quoting_at_our_cost_estimate_still_gets_a_sweet_spot():
    case = stabilise_case_with_clause()
    case["term_options"]["index_clause"] = [None]
    case["buyer"]["sustainable_margin_pct"] = 1
    thread = open_unknown_supplier(case, Negotiator(case), index_move=None, full_cost=15.2)  # quote 15: at cost
    assert thread.sweet_spot is not None
    assert thread.patience == 0.14
    assert thread.sweet_spot <= thread.quote_at_plan


def test_a_bundle_gets_no_full_cost_and_no_sweet_spot():
    """One component's cost is not the bundle's unit cost: no full cost, hence no relationship target."""
    case, terms = s01_with_quote(15.0)
    supplier_id = case["suppliers"][0]
    case["buyer"]["category_goal"] = "stabilise"
    case["facts"]["cost:320050"] = {"part": case["parts"][0], "unit_cost": 12.0, "supplier": supplier_id}
    events = [supplier_offer("ts1", terms)]

    single_part = Negotiator(case).open_thread(supplier_id, events)
    assert single_part.valuation.get("full_cost") == 12.0

    case["parts"] = case["parts"] + ["part:320051"]
    bundle = Negotiator(case).open_thread(supplier_id, events)
    assert "full_cost" not in bundle.valuation
    assert bundle.sweet_spot is None


def test_one_unparseable_quote_line_keeps_the_remaining_standard_terms():
    # the kit raises on an unexpected value; one odd line must not lose the whole quote, nor the
    # supplier at the close
    text = (
        "Quote\nOur standard terms (apply to everything not stated above)\n"
        "Payment terms (days net) none\nLead time (days) 42\nIncoterm FCA"
    )
    events = [{"from": "sales@1", "attachments": [{"name": "quote.pdf", "text": text}]}]
    with pytest.raises(ValueError):
        standard_terms(events)
    assert quoted_standard_terms(events) == {"lead_time_days": 42, "incoterm": "FCA"}
