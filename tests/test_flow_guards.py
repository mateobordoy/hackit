"""The whole case loop against hosted-like fake bots: the guards that must never bend."""

import json
from pathlib import Path

import pytest

from buyer import records
from buyer.runner import play_case
from tests.fakebot import Bot, FakeTable

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def runs(monkeypatch, tmp_path):
    monkeypatch.setattr(records, "RUNS", tmp_path / "runs")


def buyer_sheets_after(table, sid, status):
    """Our sheets to `sid` sent after it first said `status`."""
    seen, out = False, []
    for m in table.threads[sid]:
        if m.get("from") == "buyer" and seen and m.get("term_sheets"):
            out.append(m)
        if m.get("status") == status:
            seen = True
    return out


def test_a_deal_lands_near_the_floor_and_never_pushes_after_a_final_offer(pkg):
    case = load("X02")
    bot = Bot("sup:70710", 6.00, floor=5.10, std={"payment_days": 30, "contract_months": 12, "index_clause": None})
    table = FakeTable(case, [bot])
    result = play_case(table, case["case_id"], pkg)
    assert result["deal"] and result["must_haves_ok"]
    assert result["score"] > 0.6, result
    for m in buyer_sheets_after(table, "sup:70710", "final_offer"):
        # only an echo of its own standing price is allowed after its final word
        assert m["term_sheets"][0]["terms"]["unit_price"] >= bot.price - 0.01


def test_token_moves_never_happen(pkg):
    case = load("X02")
    bot = Bot("sup:70710", 6.00, floor=5.40, patience=0.9, std={"payment_days": 30, "contract_months": 12})
    table = FakeTable(case, [bot])
    play_case(table, case["case_id"], pkg)
    prices = [
        m["term_sheets"][0]["terms"]["unit_price"]
        for m in table.threads["sup:70710"]
        if m.get("from") == "buyer" and m.get("term_sheets")
    ]
    assert prices == sorted(prices), "never step backwards"
    for a, b in zip(prices[1:], prices[2:], strict=False):  # after the deep-drop hold rule's first step
        assert b == a or (b - a) / a >= 0.006 or b >= bot.price * 0.99, (a, b, prices)


def test_rfq_closes_the_cheapest_acceptable_and_respects_must_haves(pkg):
    case = load("X03")
    std = {"payment_days": 30, "lead_time_days": 52}  # its standard lead time breaks the must-have
    bots = [
        Bot("sup:70210", 36.0, floor=28.0, std=std),
        Bot("sup:70240", 34.5, floor=33.5, std=std),
        Bot("sup:70250", 38.5, floor=29.5, std=std),
    ]
    table = FakeTable(case, bots)
    result = play_case(table, case["case_id"], pkg)
    assert result["deal"] and result["must_haves_ok"], result
    assert result["terms"]["lead_time_days"] <= 42
    assert result["supplier_id"] in ("sup:70210", "sup:70250"), result
    assert result["score"] > 0.5, result


def test_no_walk_away_while_an_acceptable_sheet_stands(pkg):
    case = load("X01")
    bot = Bot("sup:70110", 139.18, floor=138.5, patience=0.05, std={"payment_days": 30})
    table = FakeTable(case, [bot])
    result = play_case(table, case["case_id"], pkg)
    assert result["deal"], "its sheet beats the alternative: a bad deal beats no deal"


def test_walks_away_when_nothing_beats_the_alternative(pkg):
    case = load("X01")
    case["buyer"]["alternative"]["terms"]["unit_price"] = 120.0  # far below anything it offers
    bot = Bot("sup:70110", 139.18, floor=136.0, std={"payment_days": 30})
    table = FakeTable(case, [bot])
    result = play_case(table, case["case_id"], pkg)
    assert result["deal"] is False
    sheets = [m for m in table.threads["sup:70110"] if m.get("from") == "buyer" and m.get("term_sheets")]
    assert all(s["term_sheets"][0]["terms"]["unit_price"] < 140 for s in sheets)


def test_a_timeout_is_rolled_back_and_the_case_goes_on(pkg):
    case = load("X02")
    bot = Bot("sup:70710", 6.00, floor=5.10, std={"payment_days": 30, "contract_months": 12})
    table = FakeTable(case, [bot], fail_once={"sup:70710"})
    result = play_case(table, case["case_id"], pkg)
    assert result["deal"]


def test_a_cheaper_alternative_it_offers_is_mirrored_before_the_close(pkg):
    case = load("X02")

    def alts(price):
        return [{"unit_price": round(price - 0.05, 2), "payment_days": 30, "contract_months": 24, "index_clause": None}]

    bot = Bot(
        "sup:70710", 6.00, floor=5.30, patience=0.2, std={"payment_days": 30, "contract_months": 12}, alternatives=alts
    )
    table = FakeTable(case, [bot])
    result = play_case(table, case["case_id"], pkg)
    assert result["deal"] and result["must_haves_ok"]


def test_every_sheet_is_valid_for_every_fixture(pkg):
    """422 means a lost round in the final: every sheet carries exactly the negotiable fields with
    allowed values (the fake table raises 422 otherwise, and the runner logs it as refused)."""
    for name, sids in {"X01": ["sup:70110"], "X02": ["sup:70710"], "X04": ["sup:70140"]}.items():
        case = load(name)
        q = case["buyer"]["current_unit_price"] * 1.1
        table = FakeTable(case, [Bot(sid, q, floor=q * 0.9) for sid in sids])
        play_case(table, case["case_id"], pkg)
        neg_notes = records.load(records.RUNS)[-1]["notes"]
        assert not [n for n in neg_notes if n["what"] == "refused"], name
