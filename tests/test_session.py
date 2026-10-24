"""The session loop: a full case against the simulator, closing before a wave cap, terminal results,
re-deciding after a refused close, the model's share of the supplier clocks, keepalive timing and
documents that cannot be read. Scripted tables, no network beyond loopback, no model."""

import copy
import secrets
import time

import pytest
from arena_kit import ArenaClient, RemoteTable, complete_offer
from arena_kit.client import ArenaError
from arena_kit.sim import load_cases
from conftest import s01_with_quote, supplier_offer

from hackit2 import session, settings
from hackit2.negotiation import Negotiator


def test_full_case_against_the_simulator_ends_in_a_scored_deal(arena_url, tmp_path, monkeypatch):
    api_key = ArenaClient(arena_url).register(f"test-{secrets.token_hex(3)}", "sim")["api_key"]
    client = ArenaClient(arena_url, api_key, rate=1000)
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    result = session.run_case(RemoteTable(client), "case:S01")
    assert result["deal"]
    assert 0 <= result["score"] <= 1


class SluggishTable:
    """One supplier that drops its price a little every round, taking `reply_s` per reply."""

    session_id = "fake"

    def __init__(self, case, reply_s):
        self.case, self.reply_s = case, reply_s
        self.supplier_id = case["suppliers"][0]
        self.price, self.closed_at = 16.0, None

    def current_reply(self):
        terms = complete_offer(self.case, {"unit_price": self.price, "payment_days": 60})
        return {
            "from": "supplier",
            "status": "open",
            "body": "Our offer.",
            "term_sheets": [{"term_sheet_id": f"ts{self.price}", "terms": terms}],
        }

    def open(self, case_id):
        return self.case, {self.supplier_id: [self.current_reply()]}

    def send(self, supplier_id, message):
        time.sleep(self.reply_s)
        self.price = round(self.price - 0.05, 2)
        return self.current_reply()

    def close(self, supplier_id, message):
        self.closed_at = time.time()
        return {"deal": message["action"]["type"] == "accept"}


def test_slow_rounds_make_the_case_close_before_the_wave_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    monkeypatch.setattr(session, "CLOSE_BEFORE_CAP_S", 0.1)  # the fixed margin alone would be too short here
    monkeypatch.setattr(session, "ROUND_MARGIN_S", 0.05)
    case = copy.deepcopy(load_cases()["case:S01"])
    table = SluggishTable(case, reply_s=0.4)
    cap = time.time() + 1.5
    result = session.run_case(table, case["case_id"], deadline=lambda: cap)
    assert result["deal"]
    assert table.closed_at < cap


class ScriptedTable:
    session_id = "fake"

    def __init__(self, case, threads, on_send=None, on_close=None, on_missed=None):
        self.case, self.threads = case, threads
        self.on_send, self.on_close, self.on_missed = on_send, on_close, on_missed
        self.close_calls = []

    def open(self, case_id):
        return self.case, self.threads

    def send(self, supplier_id, message):
        return self.on_send(supplier_id, message)

    def close(self, supplier_id, message):
        self.close_calls.append((supplier_id, message))
        return self.on_close(supplier_id, message)

    def missed(self, supplier_id, error):
        return self.on_missed(supplier_id, error)


def test_result_returned_by_a_send_ends_the_case_without_closing(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    case, terms = s01_with_quote(15.0)
    supplier_id = case["suppliers"][0]
    final_result = {"deal": False, "end_reason": "all_walked_away"}

    def must_not_close(supplier_id, message):
        raise AssertionError("closed after the arena had already ended the case")

    table = ScriptedTable(
        case,
        {supplier_id: [supplier_offer("ts1", terms)]},
        on_send=lambda s, m: {"result": final_result},
        on_close=must_not_close,
    )
    assert session.run_case(table, case["case_id"]) == final_result
    assert table.close_calls == []


@pytest.mark.parametrize(
    ("other_price", "second_action"),
    [(15.3, {"type": "accept", "term_sheet_id": "dear"}), (16.0, {"type": "walk_away"})],
)
def test_close_refused_with_409_is_decided_again_under_the_same_guards(other_price, second_action):
    """The accepted sheet vanished (409): the next choice is the other bidder's sheet while it still
    beats the fallback, otherwise a walk-away, and never a bad deal."""
    case, terms = s01_with_quote(15.0)
    case["buyer"]["alternative"] = {
        "kind": "rfq",
        "terms": complete_offer(case, {"unit_price": 15.5, "payment_days": 60}),
    }
    threads = {
        "sup:a": [supplier_offer("cheap", terms)],
        "sup:b": [supplier_offer("dear", {**terms, "unit_price": other_price})],
    }

    def close(supplier_id, message):
        if message["action"].get("term_sheet_id") == "cheap":
            raise ArenaError(409, "offer withdrawn")
        return {"deal": message["action"]["type"] == "accept"}

    def missed(supplier_id, error):
        return {"from": "arena", "status": "walked_away", "term_sheets": [], "body": "(gone)"}

    table = ScriptedTable(case, threads, on_close=close, on_missed=missed)
    session.settle(table, "case:S01", Negotiator(case), threads)
    assert [message["action"] for _, message in table.close_calls] == [
        {"type": "accept", "term_sheet_id": "cheap"},
        second_action,
    ]


def test_writing_budget_keeps_every_live_clock_serviceable():
    replied_at = {"a": 0.0, "b": 0.0, "c": 20.0}
    # a has 25 s left; b must still be answered after a 10 s send, so 15 s; c has 35 s
    assert session.writing_budget("a", replied_at, ["a", "b", "c"], 10, now=30) == pytest.approx(15)
    assert session.writing_budget("a", replied_at, ["a"], 10, now=0) == settings.MODEL_TIMEOUT_S  # capped
    assert session.writing_budget("a", replied_at, ["a", "b"], 10, now=44) < settings.MODEL_MIN_S  # no model
    assert session.writing_budget("a", replied_at, ["a", "b"], 10, now=0) == settings.MODEL_TIMEOUT_S


def test_keepalive_counts_the_next_model_call_and_round_trip():
    assert not session.needs_keepalive(20, 10, 10)
    assert session.needs_keepalive(20, 30, 10)  # below the 35 s age trigger, but the next cycle is too long
    assert session.needs_keepalive(36, 1, 1)


def test_unreadable_attachment_is_skipped_not_raised():
    class BrokenAttachmentTable:
        def attachment(self, attachment_id):
            raise ValueError("not a PDF")

    message = {"attachments": [{"attachment_id": "att:1", "name": "quote.pdf"}]}
    session.read_attachments(BrokenAttachmentTable(), "case:X", message)  # logged; the case carries on
    assert "text" not in message["attachments"][0]
