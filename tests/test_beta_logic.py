"""Beta logic changes: the touchy-supplier cap spares our first concession, the plan asks for the needed
lead time, quality-loosening packages are never adopted, and a verified index fall makes round one."""

from conftest import leverage_case

from hackit2 import settings
from hackit2.negotiation import Negotiator
from hackit2.negotiation.concession import next_step_price
from hackit2.negotiation.engine import index_in_first_round
from hackit2.negotiation.packages import loosens_quality
from hackit2.negotiation.thread import SupplierThread


def test_touchy_cap_spares_the_first_concession_but_holds_later():
    """P10: 29.95 against 31.70 jumped to 31.22 (1.5 % under its counter) and it closed next round."""
    args = {"last": 29.95, "counter": 31.70, "first_drop": 0.0, "share": 0.3, "patience": 0.16, "patience_used": 0.07}
    first = next_step_price(first_round=True, **args)
    later = next_step_price(first_round=False, **args)
    assert first < 31.70 * (1 - settings.TOUCHY_MAX_GAP) - 0.5
    assert later >= 31.70 * (1 - settings.TOUCHY_MAX_GAP) - 1e-9
    remaining = 0.16 - 0.07
    assert (31.70 - first) / 31.70 <= remaining / 2 + 1e-9  # the patience budget still holds


def lead_time_case(need=42, options=(35, 42, 56)):
    case = leverage_case()
    case["negotiable"] = [*case["negotiable"], "lead_time_days"]
    case["fixed_terms"].pop("lead_time_days", None)
    case["term_options"]["lead_time_days"] = list(options)
    case["buyer"]["need_lead_time_days"] = need
    return case


def planned_thread(lead):
    thread = SupplierThread("sup:S")
    thread.plan = {"unit_price": 13.0, "payment_days": 60, "lead_time_days": lead}
    thread.standard = {}
    return thread


def test_plan_asks_for_the_needed_lead_time_and_offers_the_slower_one_as_alternative():
    negotiator = Negotiator(lead_time_case())
    thread = planned_thread(56)
    negotiator.ask_needed_lead_time(thread)
    assert thread.plan["lead_time_days"] == 42
    assert thread.plan["unit_price"] == 13.0
    assert thread.prep_trades[0]["field"] == "lead_time_days"
    assert thread.prep_trades[0]["value"] == 56
    assert any(e["event"] == "lead_time_asked" for e in negotiator.journal)


def test_plan_within_the_need_or_without_the_option_is_left_alone():
    negotiator = Negotiator(lead_time_case())
    thread = planned_thread(35)
    negotiator.ask_needed_lead_time(thread)
    assert thread.plan["lead_time_days"] == 35 and not thread.prep_trades
    negotiator = Negotiator(lead_time_case(need=40))  # 40 is not an allowed value
    thread = planned_thread(56)
    negotiator.ask_needed_lead_time(thread)
    assert thread.plan["lead_time_days"] == 56 and not thread.prep_trades


def test_quality_loosening_packages_are_recognised():
    plan = {"ppm_commitment": 300, "chargeback_eur_per_complaint": 500}
    assert loosens_quality(plan, {"ppm_commitment": 800, "chargeback_eur_per_complaint": 500})
    assert loosens_quality(plan, {"ppm_commitment": 300, "chargeback_eur_per_complaint": 0})
    assert not loosens_quality(plan, {"ppm_commitment": 200, "chargeback_eur_per_complaint": 800})
    assert not loosens_quality({"payment_days": 30}, {"payment_days": 60})


def test_adopt_cheaper_package_never_switches_to_a_looser_quality_commitment(monkeypatch):
    """P11 run 2: the ppm 800 sheet looked EUR 4.4k cheaper in round 1; the floor was the same."""
    from hackit2 import pricing

    negotiator = Negotiator(leverage_case())
    thread = SupplierThread("sup:S")
    thread.plan = {"unit_price": 41.0, "ppm_commitment": 300}
    thread.standard, thread.valuation, thread.our_prices = {}, {}, [41.0]
    monkeypatch.setattr(pricing, "exact_package", lambda case, terms, standard: dict(terms))
    monkeypatch.setattr(pricing, "buyer_cost", lambda case, terms, *a: terms["unit_price"] * 1000)
    monkeypatch.setattr(pricing, "acceptable_cost", lambda case, terms, *a: terms["unit_price"] * 1000)
    negotiator.price_at_plan = lambda thread, terms: terms["unit_price"]
    sheets = [
        {"terms": {"unit_price": 46.5, "ppm_commitment": 300}},
        {"terms": {"unit_price": 46.0, "ppm_commitment": 800}},
    ]
    negotiator.adopt_cheaper_package(thread, sheets)
    assert thread.plan["ppm_commitment"] == 300


def arg(topic, ref):
    return {"topic": topic, "refs": [ref], "sentence": ref, "weight": 1}


def test_a_verified_index_fall_makes_the_first_round():
    evidence = [
        arg("scorecard", "sc:1"),
        arg("order_history", "po:1"),
        arg("forecast", "fc:1"),
        arg("index", "idx:X@1"),
    ]
    ordered = index_in_first_round(evidence, hedged=False)
    assert any(a["topic"] == "index" for a in ordered[: settings.CLAIMS_FIRST_ROUND])
    assert len(ordered) == len(evidence)
    already = [arg("index", "idx:X@1"), arg("scorecard", "sc:1")]
    assert index_in_first_round(already, hedged=False) == already


def test_a_hedged_supplier_hears_the_producer_price_index_before_its_raw_material():
    evidence = [arg("index", "idx:AS-14404@1"), arg("index", "idx:PPI-C2431@1"), arg("order_history", "po:1")]
    ordered = index_in_first_round(evidence, hedged=True)
    refs = [a["refs"][0] for a in ordered]
    assert refs.index("idx:PPI-C2431@1") < refs.index("idx:AS-14404@1")
