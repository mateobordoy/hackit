"""The watch UI's Scores view: the latest run of each case against earlier runs, the team's best
(pulled sessions and the arena's live board) and the reference run."""

import json
import os
import urllib.request

import pytest

from hackit2 import liveview


def write_run(root, case, session, score, at, deal=True):
    """A finished run as session.write_run_log leaves it, ending at `at` (epoch seconds)."""
    path = root / "logs" / "runs" / f"{case}_{session}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"case": f"case:{case}", "session": session, "result": {"score": score, "deal": deal}}))
    os.utime(path, (at, at))


def write_pulled(root, case, session, score):
    """A session pulled by `hackit2 pull` (only the fields the view reads)."""
    path = root / "logs" / "sessions" / "practice" / f"{case}_{session}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"case_id": f"case:{case}", "session_id": session, "result": {"score": score}}))


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Two checkouts (the integration tree and one agent's worktree), offline, no live team best."""
    v2, claude = tmp_path / "hackit-v2", tmp_path / "hackit-claude"
    for root in (v2, claude):
        (root / "logs").mkdir(parents=True)
    monkeypatch.setitem(liveview.CONFIG, "roots", [claude, v2])
    monkeypatch.setitem(liveview.CONFIG, "offline", True)
    monkeypatch.setattr(liveview, "_team", {"best": {}, "practice": {}, "at": None, "error": None})
    return v2, claude


def row(data, case):
    return next(r for r in data["rows"] if r["case"] == case)


def test_latest_run_is_compared_with_the_previous_one_across_worktrees(roots):
    v2, claude = roots
    write_run(v2, "P08", "s1", 0.538, at=1_000)
    write_run(claude, "P08", "s2", 0.715, at=2_000)  # another agent's newer run
    write_run(v2, "P08", "s2", 0.715, at=2_000)  # the same session found twice counts once
    p08 = row(liveview.scores(), "P08")
    assert p08["latest"]["source"] == "claude" and p08["latest"]["score"] == 0.715
    assert p08["previous"]["score"] == 0.538
    assert p08["change"] == pytest.approx(0.177)
    assert p08["best"] == 0.715 and len(p08["history"]) == 2


def test_team_best_is_the_higher_of_pulled_sessions_and_the_live_board(roots, monkeypatch):
    v2, _ = roots
    write_run(v2, "P11", "s1", 0.672, at=1_000)
    write_pulled(v2, "P11", "t1", 0.70)
    write_pulled(v2, "P11", "t2", 0.794)  # a teammate's better session
    p11 = row(liveview.scores(), "P11")
    assert p11["team"] == 0.794 and p11["team_pulled"] == 0.794 and p11["team_live"] is None
    assert p11["gap"] == pytest.approx(0.672 - 0.794)
    monkeypatch.setitem(liveview._team, "best", {"P11": 0.81})  # the live board knows a newer best
    p11 = row(liveview.scores(), "P11")
    assert p11["team"] == 0.81 and p11["team_live"] == 0.81


def test_reference_scores_come_from_the_digest_and_totals_count_like_the_board(roots):
    v2, _ = roots
    (v2 / "logs" / "compare").mkdir()
    digest = [
        "| Case | hackit2 | Original | Diff |",
        "|---|---|---|---|",
        "| P01 | 0.999 | 0.990 | +0.009 |",
        "| P02 | 0.735 | 0.700 | +0.035 |",
    ]
    (v2 / "logs" / "compare" / "digest.md").write_text("\n".join(digest) + "\n")
    write_run(v2, "P01", "s1", 0.9, at=1_000)
    write_run(v2, "D01", "s2", 0.99, at=1_100)  # sandbox: shown, but not in the board totals
    data = liveview.scores()
    assert row(data, "P01")["reference"] == 0.99 and row(data, "P02")["reference"] == 0.7
    assert row(data, "P02")["latest"] is None  # a case we never ran still shows its reference
    assert data["totals"]["latest_score"] == 45.0  # (0.9 + 0 for P02) / 2 practice cases x 100
    assert data["totals"]["reference"] == pytest.approx(84.5)
    assert [r["case"] for r in data["rows"]] == ["P01", "P02", "D01"]  # practice first, then sandbox


def test_unreadable_files_are_skipped(roots):
    v2, _ = roots
    (v2 / "logs" / "runs").mkdir(parents=True)
    (v2 / "logs" / "runs" / "P01_broken.json").write_text("{not json")
    write_run(v2, "P01", "s1", 0.95, at=1_000)
    assert row(liveview.scores(), "P01")["latest"]["score"] == 0.95


def test_offline_server_serves_scores_and_never_starts_the_team_reader(roots, monkeypatch):
    v2, _ = roots
    write_run(v2, "P04", "s1", 0.86, at=1_000)
    started = []
    monkeypatch.setattr(liveview, "team_loop", lambda: started.append(True))
    for key in ("live", "spend", "arena"):  # serve() sets these: restore them after the test
        monkeypatch.setitem(liveview.CONFIG, key, liveview.CONFIG[key])
    live, spend = v2 / "logs" / "live", str(v2 / "spend.json")
    server = liveview.serve(0, live=live, spend_file=spend, background=True, offline=True)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/scores"
        with urllib.request.urlopen(url, timeout=5) as reply:
            data = json.load(reply)
    finally:
        server.shutdown()
    assert started == [] and data["team"]["offline"] is True
    assert row(data, "P04")["latest"]["score"] == 0.86
