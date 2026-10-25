import json
from pathlib import Path

import pytest

from buyer import economics as eco

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def case():
    return load("X02")


def est(case, rate=0.07, **kw):
    return eco.estimate(case, {"payment_days": 30, "contract_months": 12, "index_clause": None}, rate, **kw)


def bisect_price(case, terms, e, target):
    lo, hi = 0.0, 100.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if eco.value(case, {**terms, "unit_price": mid}, e) < target else (lo, mid)
    return hi


def test_price_for_value_is_exact(case):
    e = est(case, rate=0.12, index_moves={"AS-14404": 0.059})
    terms = {
        "unit_price": 5.0,
        "payment_days": 90,
        "contract_months": 24,
        "index_clause": case["term_options"]["index_clause"][1],
    }
    target = eco.value(case, {"unit_price": 5.5, "payment_days": 30, "contract_months": 12, "index_clause": None}, e)
    assert eco.price_for_value(case, terms, e, target) == pytest.approx(bisect_price(case, terms, e, target), abs=1e-6)


def test_convert_is_identity_on_equal_terms(case):
    e = est(case)
    t = {"unit_price": 5.37, "payment_days": 60, "contract_months": 12, "index_clause": None}
    assert eco.convert(case, t, e, t) == 5.37


def test_faster_payment_costs_the_supplier_less_at_a_higher_rate(case):
    slow = {"unit_price": 5.0, "payment_days": 90, "contract_months": 12, "index_clause": None}
    fast = {**slow, "payment_days": 30}
    # a cash-short supplier gives more price for being paid sooner
    gain_low = eco.convert(case, slow, est(case, 0.05), fast) - 5.0
    gain_high = eco.convert(case, slow, est(case, 0.30), fast) - 5.0
    assert gain_high < gain_low < 0


def test_break_even_is_the_last_price_below_the_alternative(case):
    e = est(case)
    terms = {"unit_price": 5.0, "payment_days": 90, "contract_months": 24, "index_clause": None}
    cap = eco.break_even(case, terms, None, e)
    assert eco.cost(case, eco.at_price(terms, cap), None, e) < eco.c_alt(case)
    assert eco.cost(case, eco.at_price(terms, cap + 0.02), None, e) >= eco.c_alt(case)


def test_no_alternative_means_no_ceiling():
    case = load("X03")
    assert eco.break_even(case, {"unit_price": 30, "payment_days": 60, "lead_time_days": 42}) is None


def test_plan_terms_gives_valid_must_have_safe_sheets():
    for name in ("X01", "X02", "X03", "X04"):
        case = load(name)
        e = eco.estimate(case, {}, 0.07)
        price = (case["buyer"]["current_unit_price"] or case["buyer"]["fallback_unit_price"]) * 0.9
        terms, c = eco.plan_terms(case, {}, e, {}, price)
        assert set(terms) == set(case["negotiable"])
        for f, v in terms.items():
            opts = case["term_options"].get(f)
            assert f == "unit_price" or not opts or v in opts
        assert eco.must_haves_ok(case, terms, {})


def test_quality_plan_does_not_buy_a_chargeback_dearer_to_it_than_to_us():
    case = load("X04")
    e = eco.estimate(case, {"ppm_commitment": 6400}, 0.07, ppm=2400)
    terms, _ = eco.plan_terms(case, {}, e, {}, 45.0)
    assert terms["chargeback_eur_per_complaint"] == 0.0  # 2400 ppm x 500 costs it more than it saves us


def test_acceptable_refuses_must_have_breaks_and_dearer_than_alternative():
    case = load("X04")
    ok = {"unit_price": 45.0, "ppm_commitment": 800, "chargeback_eur_per_complaint": 0.0}
    assert eco.acceptable(case, ok) is not None
    assert eco.acceptable(case, {**ok, "ppm_commitment": 6400}) is None
    assert eco.acceptable(case, {**ok, "unit_price": 60.0}) is None


def test_relationship_optimum_sits_where_c_is_lowest():
    case = load("X01")
    case["buyer"]["relationship_eur_per_point"] = 40000
    e = eco.estimate(case, {"payment_days": 30}, 0.08, full_cost=130.0)
    terms = {"unit_price": 130.0, "payment_days": 30, "effective_date": "2026-11-01"}
    p = eco.relationship_optimum(case, terms, None, e, 110.0, 139.0)
    c = eco.cost(case, eco.at_price(terms, p), None, e)
    assert c <= eco.cost(case, eco.at_price(terms, p - 1), None, e)
    assert c <= eco.cost(case, eco.at_price(terms, p + 1), None, e)
    assert 125 < p < 139
