"""The reply reader and the model wrapper: answers are clamped to fixed lists, unsafe paragraphs are
counted as rejections, and a slow model is cut off by the wall clock."""

import json
import time

from conftest import WILD_ANSWERS, failing_model, leverage_case, scripted_model

from hackit2 import mailer, model


def test_expired_helper_is_marked_unused_even_if_its_answer_arrives_later(tmp_path):
    from concurrent.futures import Future

    from hackit2 import telemetry

    future = Future()
    future.telemetry_call = 99
    path = tmp_path / "deadline.jsonl"
    token = telemetry.bind(path)
    try:
        assert model.collect_json(future, 0) is None
        future.set_result('{"tone":"warm"}')
    finally:
        telemetry.unbind(token)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(e["event"], e["call"], e["status"]) for e in events] == [("model_outcome", 99, "timeout")]


def test_reader_only_returns_values_from_its_lists():
    reading = mailer.read_reply(
        scripted_model, leverage_case(), "Which volume can you commit to?", [], [15.88], ["Wages rose."]
    )
    assert (reading["mood"], reading["tactic"], reading["argument"], reading["requests"]) == (
        "irritated",
        "hold",
        1,
        ["volume"],
    )

    def outdated_tactic(system, user):
        return json.dumps({**WILD_ANSWERS, "tactic": "close_in"})

    assert mailer.read_reply(outdated_tactic, leverage_case(), "hello", [], [])["tactic"] == "steady"
    # no expected arguments: nothing can be matched
    assert mailer.read_reply(scripted_model, leverage_case(), "hello", [], [])["argument"] == 0
    assert mailer.read_reply(failing_model, leverage_case(), "hello", [], []) is None


def test_rejected_paragraph_shows_up_in_the_counters():
    before = model.counters()
    assert model.write_paragraph(lambda system, prompt: "We can reduce the price by 5 percent.", "brief") is None
    delta = model.counters_since(before)
    assert delta["paragraph.attempted"] == 1
    assert delta["paragraph.rejected"] == 1
    assert delta["paragraph.failure_rate"] == 1.0


def test_paragraph_is_abandoned_at_the_wall_clock_limit():
    calls = []

    def slow_model(system, user):
        calls.append(1)
        time.sleep(3)
        return "A fine paragraph."

    started = time.monotonic()
    assert model.write_paragraph(slow_model, "brief", 2.2) is None
    assert time.monotonic() - started < 2.8
    assert model.write_paragraph(slow_model, "brief", 1.0) is None  # too little time: never called
    assert len(calls) == 1
