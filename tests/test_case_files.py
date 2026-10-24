"""Every locally stored case file (practice, hosted or not, and the sandbox) plays through the
simulator without an exception or a logged guarded failure, whatever its structure: bundles,
three-bidder RFQs, tooling, index clauses, large term grids. The simulator's supplier is a script,
so this exercises code paths, not tactics. Case files live in logs/cases (not versioned); with none
present the parameter list is empty and pytest skips the test."""

import json
import logging
import secrets
from pathlib import Path

import pytest
from arena_kit import ArenaClient, RemoteTable
from arena_kit.sim import Sim
from conftest import running_simulator

from hackit2 import session

CASE_DIR = Path(__file__).parent.parent / "logs" / "cases"


def stored_cases():
    found = {}
    for path in sorted(CASE_DIR.glob("*.json")):
        for case in json.loads(path.read_text()):
            case = {key: value for key, value in case.items() if key != "hosted"}
            found[case["case_id"]] = case
    return found


@pytest.fixture(scope="module")
def simulated_arena():
    cases = stored_cases()
    with running_simulator(Sim(cases, clock=lambda: 1_000_000.0)) as url:
        api_key = ArenaClient(url).register(f"test-{secrets.token_hex(3)}", "sim")["api_key"]
        yield ArenaClient(url, api_key, rate=1000), cases


@pytest.mark.parametrize("case_id", sorted(stored_cases()))
def test_stored_case_plays_to_the_end_cleanly(simulated_arena, case_id, tmp_path, monkeypatch, caplog):
    client, _ = simulated_arena
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    with caplog.at_level(logging.ERROR, logger="hackit2"):
        result = session.run_case(RemoteTable(client), case_id)
    assert not caplog.records, [record.getMessage() for record in caplog.records]  # not even a guarded one
    assert "deal" in result
    if result["deal"]:
        assert 0 <= result["score"] <= 1
