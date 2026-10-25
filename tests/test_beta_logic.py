"""Beta logic changes: total buyer cost (C) guards and surplus sharing on prepared trades, quality-loosening
packages never offered or adopted, and verified index falls placed in round one."""

from conftest import LEVERAGE_QUOTE, leverage_case, supplier_quote

from hackit2 import pricing, settings
from hackit2.negotiation import Negotiator
from hackit2.negotiation.engine import index_in_first_round
from hackit2.negotiation.packages import loosens_quality
from hackit2.negotiation.thread import SupplierThread


def test_quality_loosening_packages_are_recognised():
    plan = {"ppm_commitment": 300, "chargeback_eur_per_complaint": 500}
    assert loosens_quality(plan, {"ppm_commitment": 800, "chargeback_eur_per_complaint": 500})
    assert loosens_quality(plan, {"ppm_commitment": 300, "chargeback_eur_per_complaint": 0})
    assert not loosens_quality(plan, {"ppm_commitment": 200, "chargeback_eur_per_complaint": 800})
    assert not loosens_quality({"payment_days": 30}, {"payment_days": 60})


def test_adopt_cheaper_package_never_switches_to_a_looser_quality_commitment(monkeypatch):
    """P11 run 2: the ppm 800 sheet looked EUR 4.4k cheaper in round 1; the floor was the same."""
    negotiator = Negotiator(leverage_case())
    thread = SupplierThread("sup:S")
    thread.plan = {"unit_price": 41.0, "ppm_commitment": 300}
    thread.standard, thread.valuation, thread.our_prices = {}, {}, [41.0]
    monkeypatch.setattr(pricing, "exact_package", lambda case, terms, standard: dict(terms))
    monkeypatch.setattr(pricing, "buyer_cost", lambda case, terms, *a: terms["unit_price"] * 1000)
    negotiator.price_at_plan = lambda thread, terms: terms["unit_price"]
    sheets = [
        {"terms": {"unit_price": 46.5, "ppm_commitment": 300}},
        {"terms": {"unit_price": 46.0, "ppm_commitment": 800}},
    ]
    negotiator.adopt_cheaper_package(thread, sheets)
    assert thread.plan["ppm_commitment"] == 300


def test_prepared_alternatives_skip_quality_loosening_and_unvetted_index_clauses():
    """P11 & P18: prepared trades must not volunteer looser quality or an unvetted index clause."""
    case = leverage_case()
    case["negotiable"] = [*case["negotiable"], "ppm_commitment", "index_clause"]
    case["fixed_terms"].pop("ppm_commitment", None)
    case["fixed_terms"].pop("index_clause", None)
    clause = {"series": "AS-14404", "material_share": 0.45, "deadband_pct": 0, "base_month": "2026-08"}
    case["term_options"]["ppm_commitment"] = [300, 1500]
    case["term_options"]["index_clause"] = [None, clause]
    case["buyer"]["index_paths"] = {"AS-14404": 0.06}
    negotiator = Negotiator(case)
    thread = negotiator.open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    thread.plan["ppm_commitment"] = 300
    thread.plan["index_clause"] = None
    thread.prep_trades = [
        {"field": "ppm_commitment", "value": 1500, "why": "looser quality"},
        {"field": "index_clause", "value": clause, "why": "unvetted index"},
    ]
    assert negotiator.prepared_alternatives(thread, thread.plan) == []


def test_prepared_alternatives_share_total_cost_surplus_with_buyer():
    """When a prepared trade has positive surplus, its price lowers our total cost C below main_cost."""
    case = leverage_case()
    negotiator = Negotiator(case)
    thread = negotiator.open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    thread.valuation["interest_rate"] = 0.25  # supplier values faster payment much more than our 8% rate
    main = pricing.complete_offer(case, {"unit_price": 13.0, "payment_days": 60})
    thread.plan = dict(main)
    thread.prep_trades = [{"field": "payment_days", "value": 14, "why": "faster payment"}]
    alts = negotiator.prepared_alternatives(thread, main)
    assert alts
    main_cost = pricing.buyer_cost(case, main, thread.standard, thread.valuation)
    alt_cost = pricing.buyer_cost(case, alts[0], thread.standard, thread.valuation)
    assert alt_cost < main_cost


def arg(topic, ref):
    return {"topic": topic, "refs": [ref], "sentence": ref, "weight": 1}


def test_verified_index_falls_make_the_first_round():
    evidence = [
        arg("scorecard", "sc:1"),
        arg("order_history", "po:1"),
        arg("forecast", "fc:1"),
        arg("index", "idx:X@1"),
        arg("index", "idx:Y@1"),
    ]
    ordered = index_in_first_round(evidence, hedged=False)
    first_topics = [a["topic"] for a in ordered[: settings.CLAIMS_FIRST_ROUND]]
    assert first_topics.count("index") == 2
    assert len(ordered) == len(evidence)


def test_a_hedged_supplier_hears_index_claims_after_non_index_claims():
    evidence = [
        arg("hedged", "doc:1"),
        arg("index", "idx:AS-14404@1"),
        arg("order_history", "po:1"),
        arg("scorecard", "sc:1"),
        arg("index", "idx:PPI-C2431@1"),
    ]
    ordered = index_in_first_round(evidence, hedged=True)
    first_topics = [a["topic"] for a in ordered[: settings.CLAIMS_FIRST_ROUND]]
    assert "index" not in first_topics
    assert [a["topic"] for a in ordered[-2:]] == ["index", "index"]
