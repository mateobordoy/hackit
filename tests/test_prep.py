"""The preparation team with a scripted model: every helper answer is clamped, a team without data
proposes no trades, a failing team leaves the plain engine, prepared alternatives cost us what the
main sheet costs, and the persona specialist reads the Sunday release through the ID index."""

from conftest import (
    LEVERAGE_QUOTE,
    WILD_ANSWERS,
    SundayReleasePackage,
    failing_model,
    leverage_case,
    scripted_model,
    supplier_quote,
)

from hackit2 import lessons, pricing
from hackit2.negotiation import Negotiator
from hackit2.prep import briefing, sparring


def test_lead_strategy_and_sparring_playbook_are_clamped():
    strategy = briefing.clamp_strategy(WILD_ANSWERS, leverage_case())
    assert [(trade["field"], trade["value"]) for trade in strategy["trades"]] == [("payment_days", 14)]
    assert strategy["argument_order"] == ["forecast", "should_cost"] and strategy["tone"] is None
    side = sparring.supplier_side(WILD_ANSWERS)
    assert side["arguments"] == ["Our wages rose."]  # the line with a figure and the non-string are dropped
    playbook = sparring.build_playbook(side, WILD_ANSWERS)
    assert playbook["answers"] == ["Wages are a small share of this part."]
    assert playbook["raise"] == WILD_ANSWERS["raise"]
    # without data on the supplier there is nothing "our records show"
    assert sparring.build_playbook(side, WILD_ANSWERS, informed=False)["raise"] == []


def test_team_without_data_proposes_no_trades_but_keeps_tone_and_notes(monkeypatch):
    monkeypatch.setattr(briefing, "_briefs_by_case", {})
    monkeypatch.setattr(lessons, "recall_lessons", lambda case: [])
    brief = briefing.prepare_briefs(leverage_case(), None, scripted_model)["sup:S"]  # no package, no findings
    assert brief["trades"] == [] and not brief["findings"]
    assert brief["writer_notes"] == WILD_ANSWERS["notes"]


def test_missing_or_failing_model_yields_no_briefs(monkeypatch):
    monkeypatch.setattr(briefing, "_briefs_by_case", {})
    assert briefing.prepare_briefs(leverage_case(), None, None) == {}
    assert briefing.prepare_briefs(leverage_case(), None, failing_model) == {}


def test_prepared_alternatives_cost_us_what_the_main_sheet_costs(monkeypatch):
    monkeypatch.setattr(briefing, "_briefs_by_case", {})
    case = leverage_case()
    briefs = briefing.prepare_briefs(case, None, scripted_model)
    plain = Negotiator(case).open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    negotiator = Negotiator(case, briefs=briefs)
    thread = negotiator.open_thread("sup:S", supplier_quote("open", LEVERAGE_QUOTE))
    assert (thread.opening_price, thread.plan) == (plain.opening_price, plain.plan)  # the team moves no price
    main = pricing.priced(thread.plan, thread.opening_price)
    thread.prep_trades = [
        {"field": "payment_days", "value": value, "why": ""} for value in case["term_options"]["payment_days"]
    ]
    main_cost = pricing.buyer_cost(case, main, thread.standard, thread.valuation)
    for sheet in negotiator.prepared_alternatives(thread, main):
        assert sheet["payment_days"] != main["payment_days"]
        sheet_cost = pricing.buyer_cost(case, sheet, thread.standard, thread.valuation)
        assert sheet_cost <= main_cost and main_cost - sheet_cost < 0.01 * main_cost


def test_persona_slice_reads_the_sunday_release_through_the_id_index():
    package = SundayReleasePackage()
    # routine mail (the holiday shutdown) is left out
    assert briefing.recent_letters(package, "sup:S") == ["2026-10-08 letter: text of doc:new"]
    assert briefing.recent_news(package, "sup:S") == ["2026-10-06: Sold to a group. text of news:new"]
    persona = briefing.data_slice(package, {"parts": [], "trigger_doc": None}, "sup:S", "persona")
    assert "text of doc:new" in persona and "Sold to a group" in persona and "doc:other" not in persona
