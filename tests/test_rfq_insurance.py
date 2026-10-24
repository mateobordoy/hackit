"""T15, RFQ insurance: the cheapest acceptable standing sheet must not die with its thread while we
bargain with another bidder. The arena counts each full minute a thread waits as a timeout that
uses a round; three in a row, or one after the last round, end the thread (P05: the winning bidder
waited through two timeouts in a row). Scripted threads and a fake clock: no network, no model."""

import json
import time

import pytest
from arena_kit import complete_offer
from conftest import s01_with_quote

from hackit2 import session, settings
from hackit2.negotiation import ACTIVE, SETTLED, Negotiator
from hackit2.negotiation.closing import clock_state, ends_at_next_timeout


def reply(terms, price, status="open", sheet_id="ts"):
    sheet = {"term_sheet_id": sheet_id, "terms": {**terms, "unit_price": price}}
    return {"from": "supplier", "status": status, "body": "Our offer.", "term_sheets": [sheet]}


def ours(terms, *prices):
    return {
        "from": "buyer",
        "body": "Our proposal.",
        "term_sheets": [{"terms": {**terms, "unit_price": p}} for p in prices],
    }


def with_alternative(case, price):
    case["buyer"]["alternative"] = {
        "kind": "rfq",
        "terms": complete_offer(case, {"unit_price": price, "payment_days": 60}),
    }
    return case


# --- the clock ----------------------------------------------------------------------------------


def test_each_full_minute_of_waiting_is_a_timeout_that_uses_a_round():
    assert clock_state(5, replied_at=0, silent_rounds=0, streak=0, now=30) == (5, 0, 30)
    assert clock_state(5, replied_at=0, silent_rounds=1, streak=0, now=130) == (2, 2, 50)
    assert ends_at_next_timeout(rounds_left=1, in_a_row=0)  # its last round: the next timeout ends it
    assert ends_at_next_timeout(rounds_left=4, in_a_row=2)  # the third in a row ends it
    assert not ends_at_next_timeout(rounds_left=4, in_a_row=1)


# --- the decision -------------------------------------------------------------------------------


def bidders(round_limit, prices_to_b, alternative=None):
    """Bidder a waits settled with a final 14.0 after our one message; b still bargains at 15.5."""
    case, terms = s01_with_quote(16.0)
    case["round_limit"] = round_limit
    if alternative:
        with_alternative(case, alternative)
    threads = {
        "sup:a": [reply(terms, 16.0, sheet_id="a0"), ours(terms, 14.0), reply(terms, 14.0, "final_offer", "a1")],
        "sup:b": [reply(terms, 16.5, sheet_id="b0"), ours(terms, *prices_to_b), reply(terms, 15.5, sheet_id="b1")],
    }
    negotiator = Negotiator(case)
    for supplier_id, events in threads.items():
        negotiator.open_thread(supplier_id, events[:1])
    negotiator.suppliers["sup:a"].phase, negotiator.suppliers["sup:b"].phase = SETTLED, ACTIVE
    return negotiator, threads


def decide(negotiator, threads, waited_s, streak_a=0, serve_s=20.0):
    clocks = {"sup:a": (0.0, 0, streak_a), "sup:b": (waited_s, 0, 0)}
    return negotiator.insurance(threads, clocks, now=waited_s, serve_s=serve_s)


def events_named(negotiator, name):
    return [e for e in negotiator.journal if e["event"] == name]


def test_last_round_and_no_rival_below_it_accepts_before_the_timeout():
    negotiator, threads = bidders(round_limit=2, prices_to_b=[14.5])  # b can't go below 14.5 > 14.0
    assert decide(negotiator, threads, waited_s=10) is None  # 50 s to its timeout: we serve it later
    assert decide(negotiator, threads, waited_s=45) == ("accept", "sup:a")
    assert events_named(negotiator, "insurance_accept")[0]["offer"] == "a1"


def test_a_rival_that_can_still_beat_it_gets_the_round_spent_on_a_keepalive_instead():
    negotiator, threads = bidders(round_limit=2, prices_to_b=[14.5, 13.5])  # our 13.5 sheet to b beats 14.0
    assert decide(negotiator, threads, waited_s=45) == ("keepalive", "sup:a")
    assert events_named(negotiator, "insurance_keepalive")[0]["rival"] == "sup:b"


def test_no_round_left_and_a_rival_below_with_an_acceptable_sheet_lets_it_go():
    negotiator, threads = bidders(round_limit=1, prices_to_b=[14.5, 13.5])
    assert decide(negotiator, threads, waited_s=45) is None
    assert decide(negotiator, threads, waited_s=46) is None
    assert len(events_named(negotiator, "insurance_skipped")) == 1  # logged once, not every turn
    assert not events_named(negotiator, "insurance_accept")


def test_no_round_left_and_no_other_acceptable_sheet_accepts_a_bad_deal_over_none():
    # the alternative at 15.0 makes b's 15.5 unacceptable: losing a would leave no deal at all
    negotiator, threads = bidders(round_limit=1, prices_to_b=[14.5, 13.5], alternative=15.0)
    assert decide(negotiator, threads, waited_s=45) == ("accept", "sup:a")


def test_two_timeouts_in_a_row_count_even_with_rounds_left():
    negotiator, threads = bidders(round_limit=10, prices_to_b=[14.5])
    assert decide(negotiator, threads, waited_s=50) is None  # rounds left, first timeout in a row next
    assert decide(negotiator, threads, waited_s=110) is None  # one timeout so far: the next is the second
    assert decide(negotiator, threads, waited_s=170) == ("accept", "sup:a")  # two so far: the third ends it
    negotiator, threads = bidders(round_limit=10, prices_to_b=[14.5, 13.5])
    # one timeout before this wait (a late message) and one during it: a keepalive breaks the run
    assert decide(negotiator, threads, waited_s=110, streak_a=1) == ("keepalive", "sup:a")


def test_only_a_settled_thread_is_insured():
    negotiator, threads = bidders(round_limit=2, prices_to_b=[14.5])
    negotiator.suppliers["sup:a"].phase = ACTIVE  # the loop itself answers a bargaining thread
    assert decide(negotiator, threads, waited_s=45) is None


# --- the case loop ------------------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def a_on_its_last_round(terms, rounds_used):
    """Bidder a after `rounds_used` of our messages, ending on a final 14.0."""
    events, price = [reply(terms, 16.0, sheet_id="a0")], 16.0
    for n in range(1, rounds_used + 1):
        events.append(ours(terms, 13.0 + 0.2 * n))
        price = round(price - 0.4, 2) if n < rounds_used else 14.0
        events.append(reply(terms, price, "final_offer" if n == rounds_used else "open", f"a{n}"))
    return events


class SlowBiddersTable:
    """Bidder a stands with a final offer on its last round; bidder b counters a little lower on every
    message, each reply taking `reply_s` seconds on the fake clock."""

    session_id = "fake"

    def __init__(self, case, terms, clock, reply_s=30.0):
        self.case, self.terms, self.clock, self.reply_s = case, terms, clock, reply_s
        self.threads = {
            "sup:a": a_on_its_last_round(terms, case["round_limit"] - 1),
            "sup:b": [reply(terms, 16.5, sheet_id="b0")],
        }
        self.sent, self.closed = [], []
        self.b_price = 16.5

    def open(self, case_id):
        return self.case, self.threads

    def send(self, supplier_id, message):
        self.clock.now += self.reply_s
        self.sent.append((supplier_id, self.clock.now))
        if supplier_id == "sup:a":
            return reply(self.terms, 14.0, "final_offer", f"a-keep{len(self.sent)}")
        self.b_price = round(self.b_price - 0.1, 2)
        status = "final_offer" if len(self.sent) >= 5 else "open"
        return reply(self.terms, self.b_price, status, f"b{len(self.sent)}")

    def close(self, supplier_id, message):
        self.closed.append((supplier_id, message["action"], self.clock.now))
        return {"deal": message["action"]["type"] == "accept", "supplier_id": supplier_id}

    def missed(self, supplier_id, error):
        raise AssertionError(f"unexpected 409: {error}")


@pytest.fixture
def fake_clock(monkeypatch, tmp_path):
    clock = FakeClock()
    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    return clock


def journal_of(tmp_path):
    record = json.loads(next(tmp_path.glob("*.json")).read_text())
    return [entry["event"] for entry in record["journal"]]


def two_bidder_case(alternative=None):
    case, terms = s01_with_quote(16.0)
    case = {**case, "suppliers": ["sup:a", "sup:b"], "round_limit": 6}
    return (with_alternative(case, alternative) if alternative else case), terms


def test_the_loop_keeps_the_best_offer_alive_then_accepts_it_before_its_thread_ends(fake_clock, tmp_path):
    case, terms = two_bidder_case(alternative=15.0)  # b's sheets stay above the alternative
    table = SlowBiddersTable(case, terms, fake_clock)
    result = session.run_case(table, case["case_id"])
    events = journal_of(tmp_path)
    assert events.index("insurance_keepalive") < events.index("insurance_accept")
    supplier_id, action, closed_at = table.closed[0]
    assert result["deal"] and supplier_id == "sup:a" and action["type"] == "accept"
    last_reply_from_a = max(at for sid, at in table.sent if sid == "sup:a")
    assert closed_at - last_reply_from_a < settings.THREAD_CLOCK_S  # accepted before its clock ran out
    assert any(sid == "sup:b" for sid, at in table.sent if at > last_reply_from_a)  # b was still bargaining


def test_the_loop_never_insurance_accepts_while_a_rival_below_holds_an_acceptable_sheet(fake_clock, tmp_path):
    case, terms = two_bidder_case()  # no alternative: b's sheets stay acceptable
    table = SlowBiddersTable(case, terms, fake_clock)
    session.run_case(table, case["case_id"])
    events = journal_of(tmp_path)
    assert "insurance_skipped" in events and "insurance_accept" not in events
