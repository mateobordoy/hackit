"""Frozen preparation briefs for controlled A/B runs (T21): with HACKIT_BRIEF_CACHE set, a case's briefs
are written once and then loaded without any model call; unset, the team prepares as before."""

import json

import pytest
from conftest import leverage_case, scripted_model

from hackit2 import lessons
from hackit2.prep import briefing


@pytest.fixture(autouse=True)
def fresh_team(monkeypatch):
    """No briefs remembered from other tests, no lessons."""
    monkeypatch.setattr(briefing, "_briefs_by_case", {})
    monkeypatch.setattr(lessons, "recall_lessons", lambda case: [])


def counting(model_fn):
    """The model, counting its calls."""
    calls = []

    def complete(system, user):
        calls.append(system)
        return model_fn(system, user)

    return complete, calls


def test_a_cache_miss_prepares_and_freezes_the_briefs(tmp_path, monkeypatch):
    monkeypatch.setenv("HACKIT_BRIEF_CACHE", str(tmp_path / "briefs"))
    case = leverage_case()
    complete, calls = counting(scripted_model)
    briefs = briefing.prepare_briefs(case, None, complete)
    assert calls and briefs
    frozen = tmp_path / "briefs" / f"{case['case_id'].removeprefix('case:')}.json"
    assert json.loads(frozen.read_text()) == briefs


def test_a_cache_hit_makes_no_model_call(tmp_path, monkeypatch):
    monkeypatch.setenv("HACKIT_BRIEF_CACHE", str(tmp_path))
    case = leverage_case()
    stored = {"sup:S": {"trades": [], "tone": "warm", "writer_notes": "frozen"}}
    (tmp_path / f"{case['case_id'].removeprefix('case:')}.json").write_text(json.dumps(stored))
    complete, calls = counting(scripted_model)
    assert briefing.prepare_briefs(case, None, complete) == stored
    assert calls == []


def test_unset_cache_prepares_as_before_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("HACKIT_BRIEF_CACHE", raising=False)
    monkeypatch.chdir(tmp_path)
    complete, calls = counting(scripted_model)
    assert briefing.prepare_briefs(leverage_case(), None, complete) and calls
    assert list(tmp_path.iterdir()) == []


def test_a_broken_cache_file_or_a_failed_team_is_not_frozen(tmp_path, monkeypatch):
    monkeypatch.setenv("HACKIT_BRIEF_CACHE", str(tmp_path))
    case = leverage_case()
    path = tmp_path / f"{case['case_id'].removeprefix('case:')}.json"
    path.write_text("{not json")
    complete, calls = counting(scripted_model)
    assert briefing.prepare_briefs(case, None, complete) and calls  # unreadable: prepared again
    assert json.loads(path.read_text())  # and frozen properly this time
    path.unlink()
    monkeypatch.setattr(briefing, "_briefs_by_case", {})

    def down(system, user):
        raise RuntimeError("model down")

    assert briefing.prepare_briefs(case, None, down) == {}
    assert not path.exists()  # an empty preparation is not frozen
