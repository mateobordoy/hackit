import json
import re
from pathlib import Path

from buyer import supplier, writer
from buyer.datapkg import CaseData
from buyer.evidence import EvidenceBank

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def test_claims_are_true_by_the_arena_rules(pkg):
    """Every figure we cite matches the data the arena checks against (tolerance 0.5 pp / 0.5 %)."""
    case = load("X01")
    data = CaseData(case, pkg)
    claims = EvidenceBank(data, case).for_supplier("sup:70110", 139.18)
    kinds = [c["kind"] for c in claims]
    assert "notice" in kinds and "forecast" in kinds
    assert "order_history" not in kinds  # 0 of 100 moved a supplier
    for c in claims:
        for ref in c["ids"]:
            assert data.fact(ref) is not None, ref
        if c["kind"] == "index":
            a, b = (data.fact(r)["value"] for r in c["ids"])
            pct = float(re.search(r"changed (-?[\d.]+)%", c["text"]).group(1))
            assert abs(pct - (b / a - 1) * 100) < 0.5
            assert pct < 0
        if c["kind"] == "notice":
            assert "51 days" in c["text"] and "90 days" in c["text"]
    strengths = [c["strength"] for c in claims]
    assert strengths == sorted(strengths, reverse=True)


def test_hedged_supplier_gets_material_indices_demoted(pkg):
    case = load("X02")
    bank = EvidenceBank(CaseData(case, pkg), case)
    plain = {c["ids"][0]: c["strength"] for c in bank.for_supplier("sup:70710", 6.0)}
    hedged = {c["ids"][0]: c["strength"] for c in bank.for_supplier("sup:70710", 6.0, hedged=True)}
    for ref, s in hedged.items():
        if ref.startswith(("idx:AS-", "idx:WB-", "idx:CU-")):
            assert s < plain[ref]


def test_model_paragraph_guard():
    assert writer.safe_paragraph("Thank you for your openness; we appreciate the effort your team made.")
    assert writer.safe_paragraph("We can offer 5 percent more.") is None
    assert writer.safe_paragraph("See idx:PPI-D351@2026-08 for details.") is None
    assert writer.safe_paragraph("We have no other alternative and a tight deadline.") is None
    assert writer.safe_paragraph("Dear Sir, thank you.") is None


def test_email_carries_claims_and_every_negotiable_term():
    case = load("X02")
    sheet = {"unit_price": 5.1, "payment_days": 90, "contract_months": 24, "index_clause": None}
    claims = [
        {
            "text": "Copper changed -3.0% from 2026-03 to 2026-09 (idx:WB-CU@2026-03, idx:WB-CU@2026-09).",
            "ids": ["idx:WB-CU@2026-03"],
        }
    ]
    body = writer.email(case, [sheet], claims, ["We confirm a binding volume."], first=True)
    assert claims[0]["text"] in body
    assert "EUR 5.10" in body and "payment 90 days" in body and "24 months" in body and "fixed price" in body
    assert "deadline" not in body.lower() and "budget" not in body.lower()


def test_learn_rate_recovers_the_cost_of_money():
    r, std = 0.18, 30

    def price(p_std, d):  # the price at d days worth p_std at its standard terms
        return p_std / (1 - r * (d - std) / 360)

    sheets = [
        {"unit_price": round(price(50, 60), 4), "payment_days": 60},
        {"unit_price": round(price(50, 14), 4), "payment_days": 14},
    ]
    assert abs(supplier.learn_rate(sheets, std) - r) < 0.003


def test_signals_read_the_package_phrases():
    assert "cash_need" in supplier.signals_in("The bank is watching our numbers closely.")
    assert "price_frozen" in supplier.signals_in("Head office froze price reductions until Dec 2027.")
    assert "capacity_full" in supplier.signals_in("Our order intake is very strong this year.")
    assert supplier.signals_in("Faster payment helps our cash flow; that is reflected in the price.") == []


def test_known_supplier_profile(pkg):
    case = load("X01")
    prof = supplier.profile(CaseData(case, pkg), "sup:70110")
    assert prof["owner_type"] == "family" and prof["known"]
    unknown = supplier.profile(CaseData(case, pkg), "sup:99999")
    assert unknown["signals"] == [] and not unknown["known"]
