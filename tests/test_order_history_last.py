"""With ORDER_HISTORY_LAST our order prices are cited only after every other argument, even when the
research lead ranked them first; switched off, the lead's order stands."""

import copy

from arena_kit.sim import load_cases

from hackit2 import pricing, settings
from hackit2.evidence import argument
from hackit2.negotiation import Negotiator

ARGUMENTS = [
    argument("order_history", ["po:1"], "Our last orders were placed at EUR 14.", 4),
    argument("should_cost", ["cost:1"], "Our should-cost is EUR 13.", 5),
    argument("forecast", ["fc:1"], "Our forecast is up 10%.", 2),
]


def opened_topics(monkeypatch, last):
    monkeypatch.setattr(settings, "ORDER_HISTORY_LAST", last)
    case = copy.deepcopy(load_cases()["case:S01"])
    terms = pricing.complete_offer(case, {"unit_price": 15, "payment_days": 60})
    event = {
        "from": "supplier",
        "status": "open",
        "body": "Our quote.",
        "term_sheets": [{"term_sheet_id": "q", "terms": terms}],
    }
    negotiator = Negotiator(case, briefs={"sup:x": {"argument_order": ["order_history", "forecast"]}})
    monkeypatch.setattr(negotiator.evidence_bank, "for_supplier", lambda supplier_id, quote: list(ARGUMENTS))
    return [a["topic"] for a in negotiator.open_thread("sup:x", [event]).evidence]


def test_order_history_goes_last_when_switched_on(monkeypatch):
    assert opened_topics(monkeypatch, True) == ["forecast", "should_cost", "order_history"]


def test_lead_order_stands_when_switched_off(monkeypatch):
    assert opened_topics(monkeypatch, False) == ["order_history", "forecast", "should_cost"]
