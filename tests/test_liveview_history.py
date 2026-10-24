"""The watch UI's History view: every recorded run (live files, run journals, pulled arena sessions)
listed and rebuilt as the event stream the case view replays, also when nothing runs."""

import json
import urllib.request

import pytest

from hackit2 import cli, liveview


@pytest.fixture
def root(tmp_path, monkeypatch):
    """One checkout with empty logs, read offline."""
    checkout = tmp_path / "hackit-claude"
    for folder in ("live", "runs", "sessions/practice"):
        (checkout / "logs" / folder).mkdir(parents=True)
    monkeypatch.setitem(liveview.CONFIG, "roots", [checkout])
    monkeypatch.setitem(liveview.CONFIG, "offline", True)
    monkeypatch.setattr(liveview, "_team", {"best": {}, "practice": {}, "at": None, "error": None, "created": {}})
    return checkout


def write_live(root, case, events):
    path = root / "logs" / "live" / f"case_{case}.jsonl"
    with path.open("a") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def write_journal(root, case, session, journal, score):
    path = root / "logs" / "runs" / f"{case}_{session}.json"
    path.write_text(
        json.dumps(
            {"case": f"case:{case}", "session": session, "journal": journal, "result": {"score": score, "deal": True}}
        )
    )


def pulled_session(session="s1", case="P05", created_at=None):
    """A pulled arena session: a quote, our message, its reply, a timeout and our accept."""
    sheet = {"unit_price": 30.0, "payment_days": 30}
    trace = [
        {
            "round": 0,
            "actor": "sup:A",
            "move": {
                "offer_id": "offer:A-0",
                "status": "open",
                "terms": {**sheet, "unit_price": 33.0},
                "text": "Our quote.",
            },
        },
        {
            "round": 1,
            "actor": "buyer",
            "move": {
                "type": "message",
                "supplier_id": "sup:A",
                "subject": "Re",
                "body": "We propose 30.",
                "term_sheets": [{"terms": sheet}],
                "refs": ["sc:1"],
            },
        },
        {
            "round": 1,
            "actor": "sup:A",
            "move": {
                "offer_id": "offer:A-1",
                "status": "final_offer",
                "terms": {**sheet, "unit_price": 31.0},
                "alternatives": [
                    {"offer_id": "offer:A-1-a", "terms": {**sheet, "unit_price": 30.5, "payment_days": 14}}
                ],
                "text": "Our last word.",
                "claim_results": [{"index": 0, "result": "verified"}],
            },
            "reasons": ["We hold our cost floor."],
        },
        {"round": 2, "actor": "buyer", "move": {"type": "timeout", "supplier_id": "sup:A"}},
        {"round": 2, "actor": "buyer", "move": {"type": "accept", "supplier_id": "sup:A", "offer_id": "offer:A-1"}},
    ]
    result = {
        "score": 0.8,
        "deal": True,
        "supplier_id": "sup:A",
        "c_open": 100.0,
        "c_deal": 90.0,
        "c_best": 88.0,
        "trace": trace,
    }
    backoffice = [
        {"round": 1, "supplier_id": "sup:A", "from": "Finance", "to": "Sales", "text": "Cash matters more than price."}
    ]
    state = {
        "session_id": session,
        "case_id": f"case:{case}",
        "set": "practice",
        "threads": {"sup:A": {}},
        "result": result,
        "backoffice": backoffice,
    }
    if created_at:
        state["created_at"] = created_at
    return state


def write_pulled(root, state):
    path = root / "logs" / "sessions" / "practice" / f"{state['case_id'].split(':')[1]}_{state['session_id']}.json"
    path.write_text(json.dumps(state))


def test_live_files_split_into_runs_new_and_old_format():
    old = [{"event": "session", "session_id": "a"}, {"event": "end"}, {"event": "session", "session_id": "b"}]
    new = [{"event": "case_start"}, {"event": "prep_stage"}, {"event": "session", "session_id": "c"}, {"event": "end"}]
    segments = liveview.live_segments(old + new)
    assert [[e.get("session_id") for e in s if e["event"] == "session"] for s in segments] == [["a"], ["b"], ["c"]]


def test_sources_are_merged_by_session_and_listed_newest_first(root):
    write_live(
        root,
        "D02",
        [
            {"event": "case_start", "at": 500, "title": "Shaft RFQ", "type": "rfq", "goal": "leverage"},
            {"event": "session", "at": 501, "session_id": "s2"},
            {"event": "end", "at": 600, "score": 0.7, "deal": True},
        ],
    )
    write_journal(
        root, "P05", "s1", [{"event": "session", "at": 100}, {"event": "send", "at": 110, "supplier": "sup:A"}], 0.8
    )
    write_pulled(root, pulled_session("s1"))
    write_pulled(root, pulled_session("s0", case="P01"))  # a teammate's session, no time known
    runs = liveview.history()["runs"]
    assert [r["session"] for r in runs] == ["s2", "s1", "s0"]
    s1 = runs[1]
    assert s1["has_journal"] and s1["has_arena"] and not s1["has_live"] and s1["source"] == "claude"
    assert runs[2]["source"] == "team" and runs[2]["at"] is None and runs[2]["set"] == "practice"
    assert runs[0]["title"] == "Shaft RFQ" and runs[0]["set"] == "sandbox"


def test_a_pulled_session_becomes_e_mails_prices_back_office_and_result(root):
    write_journal(
        root, "P05", "s1", [{"event": "session", "at": 100}, {"event": "send", "at": 110, "supplier": "sup:A"}], 0.8
    )
    write_pulled(root, pulled_session("s1"))
    data = liveview.run_events("s1")
    events = data["events"]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "case_start" and "session" in kinds and kinds.count("message_in") == 2
    ours = [e for e in events if e["event"] == "message_out"]
    assert ours[0]["at"] == 110 and ours[0]["sheets"][0]["unit_price"] == 30.0  # our e-mail at our send time
    assert ours[-1]["action"] == {"type": "accept", "term_sheet_id": "offer:A-1"}
    final = [e for e in events if e["event"] == "message_in"][-1]
    assert final["status"] == "final_offer" and final["offer_ids"] == ["offer:A-1", "offer:A-1-a"]
    assert final["claim_results"] == [{"index": 0, "result": "verified"}]
    state = [e for e in events if e["event"] == "thread_state"][-1]
    assert state["their_prices"] == [33.0, 31.0] and state["our_prices"] == [30.0]
    notes = [e for e in events if e["event"] == "supplier_note"]
    assert {n["sender"] for n in notes} == {"bot reasoning", "Finance"}
    assert any(e["event"] == "timeout" for e in events)
    end = next(e for e in events if e["event"] == "end")
    assert end["score"] == 0.8 and end["c_deal"] == 90.0
    assert [e["at"] for e in events] == sorted(e["at"] for e in events)


def test_a_live_file_with_e_mails_is_not_doubled_by_the_arena_copy(root):
    write_live(
        root,
        "P05",
        [
            {"event": "case_start", "at": 100},
            {"event": "session", "at": 101, "session_id": "s1"},
            {"event": "message_out", "at": 110, "supplier": "sup:A"},
            {"event": "end", "at": 200, "score": 0.8},
        ],
    )
    write_pulled(root, pulled_session("s1"))
    events = liveview.run_events("s1")["events"]
    assert sum(e["event"] == "message_out" for e in events) == 1  # the live file's own e-mail only
    assert sum(e["event"] == "end" for e in events) == 1
    assert next(e for e in events if e["event"] == "end")["c_deal"] == 90.0  # the arena's costs added
    assert any(e["event"] == "supplier_note" for e in events)  # the back office still comes from the arena
    assert liveview.run_events("nope") == {"found": False, "events": []}


def test_history_and_runs_are_served_offline(root, monkeypatch):
    write_pulled(root, pulled_session("s1", created_at=1_000))
    for key in ("live", "spend", "arena", "reference"):  # serve() sets these: restore them after the test
        monkeypatch.setitem(liveview.CONFIG, key, liveview.CONFIG[key])
    live, spend = root / "logs" / "live", str(root / "spend.json")
    server = liveview.serve(0, live=live, spend_file=spend, background=True, offline=True)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/api/history", timeout=5) as reply:
            runs = json.load(reply)["runs"]
        with urllib.request.urlopen(base + "/api/run?session=s1", timeout=5) as reply:
            run = json.load(reply)
    finally:
        server.shutdown()
    assert runs[0]["session"] == "s1" and runs[0]["at"] == 1_000
    assert run["found"] and run["run"]["case"] == "P05"


def test_pull_keeps_the_session_start_time(tmp_path, monkeypatch):
    class FakeClient:
        def sessions(self, case_set):
            return [{"session_id": "s9", "case_id": "case:P01", "status": "ended", "created_at": 1_234.5}]

        def session(self, session_id):
            return {"session_id": session_id, "case_id": "case:P01", "result": {"trace": [{"round": 0}]}}

        def backoffice(self, session_id):
            return []

    monkeypatch.setattr(cli, "SESSION_LOGS", tmp_path)
    cli.pull_sessions(FakeClient(), "practice")
    saved = json.loads((tmp_path / "practice" / "P01_s9.json").read_text())
    assert saved["created_at"] == 1_234.5
