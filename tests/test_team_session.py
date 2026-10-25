"""A whole case with the preparation team and the reply reader on a scripted model: it ends in a deal,
the journal shows the brief and the reader, the debrief records a lesson, and with a failing model the
plain engine still closes the case."""

import json
import secrets

from arena_kit import ArenaClient, RemoteTable
from arena_kit.sim import load_cases
from conftest import WILD_ANSWERS, failing_model, scripted_model

from hackit2 import session
from hackit2.prep import alpha


def test_team_case_closes_and_records_a_lesson(arena_url, tmp_path, monkeypatch):
    api_key = ArenaClient(arena_url).register(f"test-{secrets.token_hex(3)}", "sim")["api_key"]
    client = ArenaClient(arena_url, api_key, rate=1000)
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    monkeypatch.setattr(session.lessons, "LESSONS_FILE", tmp_path / "lessons.jsonl")
    monkeypatch.setattr(alpha, "_cache", {})
    case = load_cases()["case:S01"]
    result = session.run_case(RemoteTable(client, case), "case:S01", complete=scripted_model)
    assert result["deal"] and 0 <= result["score"] <= 1
    journal = json.loads(next(tmp_path.glob("S01_*.json")).read_text())["journal"]
    assert {"brief", "reader"} <= {entry["event"] for entry in journal}
    assert WILD_ANSWERS["lesson"] in session.lessons.LESSONS_FILE.read_text()
    recalled = session.lessons.recall_lessons(case)
    # the lead sees what happened in the run, never one run's moral
    assert recalled and WILD_ANSWERS["lesson"] not in recalled[0]

    monkeypatch.setattr(alpha, "_cache", {})
    result = session.run_case(RemoteTable(client, case), "case:S01", complete=failing_model)
    assert result["deal"]  # the plain engine plays on
