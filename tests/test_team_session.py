"""A whole case with the preparation team and the reply reader on a scripted model: it ends in a deal,
the journal shows the brief and the reader, the lessons file is only read, and with a failing model
the plain engine still closes the case."""

import json
import secrets

from arena_kit import ArenaClient, RemoteTable
from arena_kit.sim import load_cases
from conftest import failing_model, scripted_model

from hackit2 import lessons, session
from hackit2.prep import alpha

ROW = {
    "goal": "leverage",
    "type": "price_increase",
    "bidders": 1,
    "owners": ["family"],
    "signals": [],
    "deal": True,
    "score": 0.8,
    "lesson": "Offer faster payment to a shop short of cash.",
    "threads": [{"opened": 0.85, "ours": [0.85], "theirs": [0.95], "tactics": [], "trades": []}],
}


def test_team_case_closes_and_only_reads_the_lessons(arena_url, tmp_path, monkeypatch):
    api_key = ArenaClient(arena_url).register(f"test-{secrets.token_hex(3)}", "sim")["api_key"]
    client = ArenaClient(arena_url, api_key, rate=1000)
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    memory = tmp_path / "lessons.jsonl"
    memory.write_text(json.dumps(ROW) + "\n")
    monkeypatch.setattr(lessons, "LESSONS_FILE", memory)
    monkeypatch.setattr(alpha, "_cache", {})
    case = load_cases()["case:S01"]
    result = session.run_case(RemoteTable(client, case), "case:S01", complete=scripted_model)
    assert result["deal"] and 0 <= result["score"] <= 1
    journal = json.loads(next(tmp_path.glob("S01_*.json")).read_text())["journal"]
    assert {"brief", "reader"} <= {entry["event"] for entry in journal}
    assert memory.read_text() == json.dumps(ROW) + "\n"  # nothing is written during a run
    recalled = lessons.recall_lessons({**case, "buyer": {**case["buyer"], "category_goal": "leverage"}})
    # the lead sees what happened in an earlier run, never one run's moral
    assert recalled and ROW["lesson"] not in recalled[0]

    monkeypatch.setattr(alpha, "_cache", {})
    result = session.run_case(RemoteTable(client, case), "case:S01", complete=failing_model)
    assert result["deal"]  # the plain engine plays on
