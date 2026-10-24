"""The watch UI shows running tests from every worktree: live files of all hackit-* checkouts, and the
team's arena sessions that are still open (from the session list read once a minute)."""

import json

import pytest

from hackit2 import liveview


@pytest.fixture
def worktrees(tmp_path, monkeypatch):
    """Two checkouts: the UI runs in hackit-v2, codex writes its own live files."""
    v2, codex = tmp_path / "hackit-v2", tmp_path / "hackit-codex"
    for root in (v2, codex):
        (root / "logs" / "live").mkdir(parents=True)
    monkeypatch.chdir(v2)
    monkeypatch.setitem(liveview.CONFIG, "live", v2 / "logs" / "live")
    monkeypatch.setitem(liveview.CONFIG, "roots", [v2, codex])
    monkeypatch.setitem(liveview.CONFIG, "offline", True)
    monkeypatch.setattr(liveview, "_team", {"best": {}, "practice": {}, "at": None, "error": None, "created": {}})
    return v2, codex


def live(root, case, events):
    (root / "logs" / "live" / f"case_{case}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_live_files_of_every_worktree_are_listed_and_served(worktrees):
    v2, codex = worktrees
    live(v2, "P05", [{"event": "case_start", "at": 1}, {"event": "session", "at": 2, "session_id": "a"}])
    live(codex, "P05", [{"event": "case_start", "at": 3}, {"event": "session", "at": 4, "session_id": "b"}])
    cases = {c["case"]: c for c in liveview.all_cases()}
    assert set(cases) == {"P05", "P05@codex"}  # the same case from two worktrees does not collide
    assert cases["P05"]["source"] == "v2" and cases["P05@codex"]["source"] == "codex"
    events = liveview.case_events("P05@codex", 0)["events"]
    assert [e.get("session_id") for e in events if e["event"] == "session"] == ["b"]


def test_open_arena_sessions_show_as_running(worktrees, monkeypatch):
    sessions = {
        "s-open": {
            "session_id": "s-open",
            "case_id": "case:P07",
            "status": "open",
            "created_at": 50.0,
            "set": "practice",
        },
        "s-done": {"session_id": "s-done", "case_id": "case:P03", "status": "ended", "created_at": 40.0},
    }
    monkeypatch.setitem(liveview._team, "sessions", sessions)
    runs = liveview.history()["runs"]
    assert runs[0]["session"] == "s-open" and runs[0]["running"] and runs[0]["case"] == "P07"
    assert all(r["session"] != "s-done" for r in runs)  # ended and not recorded locally: nothing to show
    assert liveview.running_sessions().keys() == {"s-open"}


def test_an_observer_session_reports_model_calls_as_unknown(worktrees):
    _, codex = worktrees
    live(
        codex,
        "P07",
        [
            {"event": "case_start", "at": 1, "observer": True, "trial": "P07-pair1-off"},
            {"event": "session", "at": 2, "session_id": "c"},
        ],
    )
    case = next(c for c in liveview.all_cases() if c["case"] == "P07@codex")
    assert case["model_calls"] is None and case["observer"] and case["trial"] == "P07-pair1-off"
