"""Setup on the real hosted practice case files (saved from GET /v1/cases): every opening sheet is
valid, and the known structural trades are found."""

import json
from pathlib import Path

import pytest

from buyer.policy import Negotiator

PRACTICE = Path(__file__).parent / "fixtures" / "practice"
QUOTES = {"P02": ("sup:70510", {"unit_price": 370.96, "payment_days": 30, "index_clause": None})}


def case(name):
    path = PRACTICE / f"{name}.json"
    if not path.exists():
        pytest.skip("practice case files not saved")
    return json.loads(path.read_text(encoding="utf-8"))


def opening(sid, terms):
    return [
        {
            "from": f"sales@{sid[4:]}",
            "status": "open",
            "body": "Our quote.",
            "attachments": [],
            "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0", "terms": terms}],
        }
    ]


def test_p02_trades_a_copper_clause_at_the_relationship_optimum(pkg):
    c = case("P02")
    sid, quote = QUOTES["P02"]
    neg = Negotiator(c, pkg)
    t = neg.setup(sid, opening(sid, quote))
    assert t.terms["index_clause"] and t.terms["index_clause"]["deadband_pct"] == 5
    assert t.target and t.anchor >= t.target - 0.01
    assert 350 < t.anchor < 371  # Nordmark agreed at once at 358 with this clause [pc]


@pytest.mark.parametrize("name", [p.stem for p in sorted(PRACTICE.glob("P*.json"))])
def test_every_practice_opening_sheet_is_valid(pkg, name):
    c = case(name)
    neg = Negotiator(c, pkg)
    base = c["buyer"].get("current_unit_price") or c["buyer"]["fallback_unit_price"]
    for sid in c["suppliers"]:
        t = neg.setup(sid, opening(sid, {"unit_price": round(base * 1.08, 2)}))
        assert set(t.terms) == set(c["negotiable"])
        for f, v in t.terms.items():
            opts = c["term_options"].get(f)
            assert f == "unit_price" or not opts or v in opts, (f, v)
        assert 0 < t.anchor < base * 1.08
