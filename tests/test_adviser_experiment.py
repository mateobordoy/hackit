"""The experiment must freeze used fallbacks and may never retry an uncertain session open."""

import importlib.util
import json
from pathlib import Path

import pytest

from hackit2 import model, reply_reader

spec = importlib.util.spec_from_file_location(
    "adviser_experiment", Path(__file__).resolve().parents[1] / "scripts/adviser_experiment.py"
)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def test_attempt_ledger_rejects_duplicates_and_enforces_the_cohort_cap(tmp_path):
    (tmp_path / "cohort.json").write_text(json.dumps({"max_sessions": 2}))
    experiment.reserve_trial(tmp_path, "first")
    with pytest.raises(RuntimeError, match="already attempted"):
        experiment.reserve_trial(tmp_path, "first")
    experiment.reserve_trial(tmp_path, "second")
    with pytest.raises(RuntimeError, match="cap reached"):
        experiment.reserve_trial(tmp_path, "third")
    assert len(json.loads((tmp_path / "attempts.json").read_text())["attempts"]) == 2


def test_opening_cache_freezes_used_fallbacks_even_if_a_later_call_would_succeed(tmp_path, monkeypatch):
    calls = {"reader": 0, "writer": 0}

    def read(*args, **kwargs):
        calls["reader"] += 1
        return None if calls["reader"] == 1 else {"tactic": "hold"}

    def write(*args, **kwargs):
        calls["writer"] += 1
        return None if calls["writer"] == 1 else "We appreciate your reply."

    monkeypatch.setattr(reply_reader, "read_reply", read)
    monkeypatch.setattr(model, "write_paragraph", write)
    hits = experiment.install_opening_cache(tmp_path)
    for budget in (1, 15):
        assert reply_reader.read_reply(None, {"case_id": "case:X"}, "Opening", [], [15], timeout=budget) is None
        assert model.write_paragraph(None, "Brief\nQUESTION ALLOWED: yes", timeout=budget) is None
    assert calls == {"reader": 1, "writer": 1}
    assert hits == {"reader_hit": 1, "reader_miss": 1, "writer_hit": 1, "writer_miss": 1}
    assert reply_reader.read_reply(None, {"case_id": "case:X"}, "Counter", [14], [15]) == {"tactic": "hold"}
    assert model.write_paragraph(None, "Brief\nQUESTION ALLOWED: no") == "We appreciate your reply."


def test_opening_cache_does_not_reuse_a_paragraph_for_a_different_prompt(tmp_path, monkeypatch):
    calls = []

    def write(complete, prompt, timeout=None, supplier_id=None):
        calls.append(prompt)
        return "We appreciate your reply."

    monkeypatch.setattr(model, "write_paragraph", write)
    monkeypatch.setattr(reply_reader, "read_reply", lambda *args, **kwargs: None)
    experiment.install_opening_cache(tmp_path)
    for prompt in ("A\nQUESTION ALLOWED: yes", "B\nQUESTION ALLOWED: yes", "A\nQUESTION ALLOWED: yes"):
        model.write_paragraph(None, prompt)
    assert len(calls) == 2
