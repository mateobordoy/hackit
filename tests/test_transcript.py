"""The delivered-message transcript: it survives a restart, never turns a draft, an argument or a
request into an agreement, and reaches the existing model prompts without an extra model call."""

import copy
import json

from arena_kit.sim import load_cases

from hackit2 import mailer


def test_transcript_keeps_contradictions_and_exact_sheets_without_aliasing():
    case = copy.deepcopy(load_cases()["case:S01"])
    events = [
        {
            "from": "supplier",
            "body": "We are hedged.",
            "term_sheets": [{"term_sheet_id": "old", "terms": {"unit_price": 12}}],
        },
        {
            "from": "buyer",
            "body": "Could you accept earlier payment?",
            "refs": ["doc:proof"],
            "term_sheets": [{"terms": {"unit_price": 10, "payment_days": 14}}],
        },
        {
            "from": "supplier",
            "body": "That is not our reality: material is covered at fixed prices.",
            "term_sheets": [{"term_sheet_id": "current", "terms": {"unit_price": 11, "payment_days": 30}}],
        },
    ]
    untouched = copy.deepcopy(events)
    built = mailer.build_transcript(case, events)
    assert built == mailer.build_transcript(case, json.loads(json.dumps(events)))
    assert built["standing_supplier_sheets"][0]["terms"]["payment_days"] == 30
    assert built["delivered_history"][1]["sheets"][0]["terms"]["payment_days"] == 14
    assert built["delivered_history"][1]["claim_feedback"] == []
    assert "covered at fixed prices" in built["delivered_history"][2]["text_excerpt"]
    built["delivered_history"][0]["sheets"][0]["terms"]["unit_price"] = 1  # a deep copy, not a view
    assert events == untouched


def test_long_bodies_are_flagged_as_cut_and_no_draft_is_recorded():
    case = load_cases()["case:S01"]
    built = mailer.build_transcript(case, [{"from": "supplier", "body": "x" * 900, "term_sheets": []}])
    assert len(built["delivered_history"]) == 1
    assert built["delivered_history"][0]["text_truncated"]
    assert len(built["delivered_history"][0]["text_excerpt"]) == 800
    assert built["standing_supplier_sheets"] == []
    assert "draft_proposal" not in built


def test_reader_and_writer_prompts_carry_the_history_in_one_call():
    case = load_cases()["case:S01"]
    history = mailer.build_transcript(case, [{"from": "supplier", "body": "We refused the earlier payment request."}])
    prompts = []

    def recording_model(system, user):
        prompts.append(user)
        return json.dumps({"tactic": "steady", "notes": "Payment request was refused."})

    reading = mailer.read_reply(recording_model, case, "Our offer stands.", [], [], history=history)
    assert reading["tactic"] == "steady" and len(prompts) == 1
    assert "We refused the earlier payment request." in prompts[0]
    prompt = mailer.paragraph_prompt(case, {}, "Our offer stands.", [], "warm", False, history=history)
    assert "We refused the earlier payment request." in prompt
    assert "unverified" in prompt
    assert "A requested or proposed term is not an accepted one" in prompt
