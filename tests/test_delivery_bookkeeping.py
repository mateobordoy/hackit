"""A failed or late proposal must not spend facts or advance the price path."""

import pytest
from conftest import LEVERAGE_QUOTE, leverage_case, supplier_quote

from hackit2 import mailer
from hackit2.negotiation import ACTIVE, SETTLED, Negotiator


def opened():
    negotiator = Negotiator(leverage_case())
    events = supplier_quote("open", LEVERAGE_QUOTE)
    thread = negotiator.open_thread("sup:S", events)
    thread.requests = ["proof"]
    negotiator.evidence_bank.ids = lambda prefix: ["fc:360010@2026-10"]
    negotiator.evidence_bank.fact = lambda ref: {"volume_per_year": 100, "change_pct": 5}
    return negotiator, thread, events


def proposal(negotiator, thread, events):
    return negotiator.dispatch(thread, [thread.plan], [], events, thread.quote_at_plan)


def test_late_forecast_attachment_is_available_until_confirmed():
    negotiator, thread, events = opened()
    assert proposal(negotiator, thread, events)["message"]["attachments"][0]["name"] == "forecast.csv"
    assert "forecast" not in thread.files_sent
    negotiator.undo_delivery(thread.supplier_id)
    retry = proposal(negotiator, thread, events)
    assert retry["message"]["attachments"][0]["name"] == "forecast.csv"
    negotiator.confirm_delivery(thread.supplier_id)
    assert "forecast" in thread.files_sent
    assert "attachments" not in proposal(negotiator, thread, events)["message"]


def test_template_fallback_discards_failed_proposal_without_counting_a_timeout(monkeypatch):
    negotiator, thread, events = opened()

    def broken_email(*args, **kwargs):
        raise ValueError("cannot render proposal")

    monkeypatch.setattr(mailer, "compose_email", broken_email)
    with pytest.raises(ValueError, match="cannot render"):
        proposal(negotiator, thread, events)
    holding = negotiator.safe_message(thread.supplier_id, {thread.supplier_id: events})
    assert holding["message"]["term_sheets"] == []
    negotiator.confirm_delivery(thread.supplier_id)
    assert thread.our_prices == []
    assert thread.gap_history == []
    assert thread.last_primary_sent is None
    assert thread.in_flight is None
    assert thread.phase == ACTIVE
    assert thread.late_rounds == 0
    assert thread.files_sent == set()


@pytest.mark.parametrize("status", ["final_offer", "agreed"])
def test_failed_move_after_frozen_offer_settles_instead_of_sending_a_holding_message(status):
    negotiator, thread, events = opened()
    events[-1]["status"] = status
    assert negotiator.safe_message(thread.supplier_id, {thread.supplier_id: events}) is None
    assert thread.phase == SETTLED
