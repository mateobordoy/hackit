"""Trait patterns on the wording of the Sunday release letters and news."""

from hackit2.supplier_profile import traits_in


def test_release_wording_is_recognised_as_traits():
    assert "price_frozen" in traits_in("our price list 2027 applies to all customers without exception")
    assert "price_frozen" in traits_in("our board has ruled out individual price reductions")
    assert "price_frozen" in traits_in("we can no longer grant individual price reductions")
    assert "price_frozen" in traits_in("prices will be set centrally by the group")
    assert "hedged" in traits_in("we bought our bright and cold-drawn bar requirement forward at fixed prices")
    assert traits_in("we are happy to discuss individual price reductions for volume") == []
    assert "cash_need" in traits_in("Odlewnia Kalwisz Sp. z o.o. files for insolvency proceedings")
    assert "cash_need" in traits_in("a credit insurer has withdrawn its limits; suppliers ask for prepayment")
