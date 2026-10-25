"""Hypothesis learning: the agents' testable moves are cleaned and merged, code plays the most probable
valid one without touching our price, and fixed rules update the probabilities from the reply."""

import pytest
from conftest import LEVERAGE_QUOTE, leverage_case, supplier_offer, supplier_quote

from hackit2 import hypotheses, pricing, settings
from hackit2.negotiation import Negotiator

SOURCES = {"source:fin": {}, "source:ops": {}}


def raw(*items):
    return {"hypotheses": list(items)}


def trade(value, p=0.6, reaction="takes_term", refs=("source:fin",)):
    return {
        "move": {"kind": "trade", "field": "payment_days", "value": value},
        "reaction": reaction,
        "probability": p,
        "rationale": "it pays its own suppliers late",
        "source_ids": list(refs),
        "uncertainty": "one old record",
    }


# --- cleaning and merging --------------------------------------------------------------------------


def test_only_well_formed_grounded_moves_survive():
    case = leverage_case()
    items = [
        trade(14),
        trade(45),  # not one of the case's options
        {**trade(14), "move": {"kind": "trade", "field": "unit_price", "value": 10}},  # price is code's
        trade(30, refs=("source:invented",)),  # cites nothing it was given
        trade(30, p=1.4),  # not a probability
        {**trade(30), "move": {"kind": "argument", "topic": "astrology"}, "reaction": "price_drop"},
        {**trade(30), "move": {"kind": "argument", "topic": "should_cost"}, "reaction": "takes_term"},
    ]
    kept = hypotheses.clean(raw(*items), SOURCES, case)
    assert [(h["move"]["value"], h["reaction"]) for h in kept] == [(14, "takes_term")]
    certain = hypotheses.clean(raw(trade(30, p=1.0)), SOURCES, case)
    assert certain[0]["p"] == hypotheses.P_MAX  # a stated certainty is capped: later evidence must still count


def test_the_same_move_from_two_agents_merges_and_probabilities_are_not_normalised():
    case = leverage_case()
    by_agent = {
        "solvent_finance": hypotheses.clean(raw(trade(14, p=0.7), trade(30, p=0.6)), SOURCES, case),
        "sentinel_operations": hypotheses.clean(raw(trade(14, p=0.5, refs=("source:ops",))), SOURCES, case),
    }
    merged = hypotheses.merge(by_agent)
    assert [h["id"] for h in merged] == ["H1", "H2"]
    h14 = next(h for h in merged if h["move"]["value"] == 14)
    assert h14["agents"] == ["sentinel_operations", "solvent_finance"]
    assert h14["source_ids"] == ["source:fin", "source:ops"] and h14["p0"] == pytest.approx(0.6)
    assert sum(h["p0"] for h in merged) > 1  # independent hypotheses keep their own estimates


# --- deterministic updates ---------------------------------------------------------------------------


def started(move_value=14, reaction="takes_term", p=0.6):
    h = hypotheses.merge(
        {"a": hypotheses.clean(raw(trade(move_value, p=p, reaction=reaction)), SOURCES, leverage_case())}
    )
    return hypotheses.start(h)[0]


def reply(sheets, body="", previous=10.0, counter=10.0, status="open", claims=None, before=None, pace=0.005):
    return {
        "previous_step": pace,
        "sheets": sheets,
        "body": body,
        "previous_counter": previous,
        "counter": counter,
        "status": status,
        "claims": claims or {},
        "field_before": {"payment_days": 60} if before is None else before,
    }


def test_taking_the_term_supports_and_raises_the_probability_by_the_fixed_ratio():
    h = started(p=0.6)
    outcome, reason = hypotheses.observe(h, reply([{"payment_days": 60}, {"payment_days": 14}]))
    assert outcome == "supported" and "payment_days = 14" in reason
    hypotheses.update(h, outcome, reason, 1)
    assert h["p"] == pytest.approx(0.818, abs=1e-3)  # odds 1.5 x 3 = 4.5
    assert h["updates"][-1] == {
        "round": 1,
        "outcome": "supported",
        "reason": reason,
        "p_before": 0.6,
        "p_after": h["p"],
    }


def test_an_explicit_refusal_or_a_move_elsewhere_refutes():
    h = started(p=0.6)
    text = "We cannot offer shorter payment terms, our bank requires 60 days."
    outcome, _ = hypotheses.observe(h, reply([{"payment_days": 60}], body=text))
    assert outcome == "refuted"
    hypotheses.update(h, outcome, "refused", 1)
    assert h["p"] == pytest.approx(0.333, abs=1e-3)  # odds 1.5 / 3 = 0.5
    moved, _ = hypotheses.observe(started(), reply([{"payment_days": 45}], before={"payment_days": 60}))
    assert moved == "refuted"  # it moved the term, but not to what we offered


def test_silence_is_ambiguous_and_leaves_the_probability_unchanged():
    h = started(p=0.6)
    outcome, _ = hypotheses.observe(h, reply([{"payment_days": 60}], body="Thank you, we keep our offer."))
    assert outcome == "ambiguous"
    hypotheses.update(h, outcome, "no signal", 1)
    assert h["p"] == 0.6 and h["updates"][-1]["outcome"] == "ambiguous"


def test_price_drop_rules():
    h = started(reaction="price_drop")
    # its own pace was 0.5 % a round: a 1.5 % fall is clearly faster, a 1 % fall is not
    assert hypotheses.observe(h, reply([], previous=10.0, counter=9.85))[0] == "supported"
    assert hypotheses.observe(h, reply([], previous=10.0, counter=9.90))[0] == "ambiguous"
    # on its first counter there is no pace to compare with: a fall alone proves nothing
    assert hypotheses.observe(h, reply([], previous=10.0, counter=9.0, pace=None))[0] == "ambiguous"
    assert hypotheses.observe(h, reply([], previous=10.0, counter=10.0, status="final_offer"))[0] == "refuted"
    arg = {**h, "move": {"kind": "argument", "topic": "index"}}
    assert hypotheses.observe(arg, reply([], counter=9.99, claims={"index": "contradicted"}))[0] == "refuted"
    assert hypotheses.observe(arg, reply([], counter=10.0, claims={"index": "verified"}))[0] == "refuted"


def test_probabilities_stay_inside_their_bounds():
    h = started(p=0.95)
    for _ in range(5):
        hypotheses.update(h, "supported", "again", 1)
    assert h["p"] == hypotheses.P_MAX
    for _ in range(9):
        hypotheses.update(h, "refuted", "again", 1)
    assert h["p"] == hypotheses.P_MIN


# --- the engine ---------------------------------------------------------------------------------------


def brief_with(*items):
    return {
        "trades": [],
        "hypotheses": hypotheses.merge({"atlas": hypotheses.clean(raw(*items), SOURCES, leverage_case())}),
    }


def opening(monkeypatch, on, brief):
    monkeypatch.setattr(settings, "HYPOTHESES", on)
    case = leverage_case()
    events = supplier_quote("open", LEVERAGE_QUOTE)
    negotiator = Negotiator(case, briefs={"sup:S": brief})
    negotiator.open_thread("sup:S", events)
    return negotiator, case, events


def test_the_most_probable_valid_hypothesis_is_played_at_no_extra_buyer_cost(monkeypatch):
    argument = {**trade(30, p=0.9, reaction="price_drop"), "move": {"kind": "argument", "topic": "should_cost"}}
    negotiator, case, events = opening(monkeypatch, True, brief_with(trade(30, p=0.4), trade(14, p=0.7), argument))
    sheets = [s["terms"] for s in negotiator.next_message("sup:S", {"sup:S": events})["message"]["term_sheets"]]
    thread = negotiator.suppliers["sup:S"]
    main, played = sheets[0], sheets[1]
    assert played["payment_days"] == 14 and main["payment_days"] == 60
    cost = lambda terms: pricing.buyer_cost(case, terms, thread.standard, thread.valuation)  # noqa: E731
    assert cost(played) <= cost(main)  # code prices the move: never dearer for us
    by_id = {h["id"]: h for h in thread.hypotheses}
    assert by_id["H1"]["valid"] is False and "evidence" in by_id["H1"]["why_not"]  # p 0.9, but nothing to cite
    assert [e for e in negotiator.journal if e["event"] == "hypothesis_played"][0]["id"] == "H2"
    assert thread.in_flight["hypotheses"]["ids"] == ["H2"]


def test_the_reply_updates_only_what_was_tested(monkeypatch):
    negotiator, case, events = opening(monkeypatch, True, brief_with(trade(14, p=0.6), trade(30, p=0.5)))
    move = negotiator.next_message("sup:S", {"sup:S": events})
    negotiator.confirm_delivery("sup:S")
    events += [
        {**move["message"], "from": "buyer"},
        supplier_offer("offer:S-2", {"unit_price": 15.5, "payment_days": 14}),
    ]
    negotiator.next_message("sup:S", {"sup:S": events})
    by_move = {h["move"]["value"]: h for h in negotiator.suppliers["sup:S"].hypotheses}
    assert by_move[14]["updates"][0]["outcome"] == "supported" and by_move[14]["p"] == pytest.approx(0.818, abs=1e-3)
    assert by_move[30]["updates"] == [] and by_move[30]["p"] == 0.5  # not played, not judged


def test_switched_off_the_engine_ignores_hypotheses(monkeypatch):
    negotiator, _, events = opening(monkeypatch, False, brief_with(trade(14, p=0.9)))
    sheets = negotiator.next_message("sup:S", {"sup:S": events})["message"]["term_sheets"]
    assert len(sheets) == 1 and negotiator.suppliers["sup:S"].hypotheses == []
    assert not any(e["event"].startswith("hypothes") for e in negotiator.journal)
