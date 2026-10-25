"""The Showcase storyboard: built from a recorded sim run, every step carries a caption and map edges,
the main thread's rounds are complete, and a bundled run is what the button opens by default."""

from buyer import config, liveview, showcase
from buyer.runner import play_batch

EDGES = {
    ("arena", "runner"), ("runner", "arena"), ("runner", "policy"), ("policy", "runner"),
    ("supplier", "policy"), ("reader", "policy"), ("evidence", "policy"), ("economics", "policy"),
    ("memory", "belief"), ("belief", "planner"), ("planner", "policy"), ("economics", "planner"),
    ("policy", "writer"), ("writer", "runner"), ("runner", "records"), ("runner", "memory"),
    ("policy", "belief"),
}  # fmt: skip


def test_storyboard_from_a_sim_rfq(sim_client, pkg, monkeypatch, tmp_path):
    case = next(c for c in sim_client.cases("practice") if c["case_id"] == "case:X03")
    [(_, result)] = play_batch(sim_client, [case], repeat=1, parallel=1)
    assert result["deal"]
    monkeypatch.setitem(liveview.CONFIG, "roots", [])
    monkeypatch.setitem(liveview.CONFIG, "live", config.LOG_DIR / "live")
    monkeypatch.setattr(showcase, "BUNDLED", tmp_path / "none")
    monkeypatch.setattr(showcase, "HIDE_SIM", False)
    sb = liveview.showcase_story()
    assert sb["found"] and sb["case_id"] == "case:X03" and sb["main"] == result["supplier_id"]
    kinds = [s["kind"] for s in sb["steps"]]
    assert kinds[0] == "intro" and kinds[-1] == "outro" and kinds[-2] == "result"
    assert {"prep", "send", "reply", "plan", "close"} <= set(kinds)
    for s in sb["steps"]:
        assert s["caption"] and s["title"]
        assert all(tuple(f) in EDGES for f in s["flow"]), s["flow"]
    main_rounds = sb["rounds"][sb["main"]]
    assert main_rounds[0]["round"] == 0 and main_rounds[-1]["status"] in ("agreed", "final_offer", "open")
    assert all(r.get("p10") is not None for r in main_rounds)

    # frozen as the bundled default, it is what the button opens, also with no live files left
    session = sb["session"]
    showcase.save(liveview.live_runs()[-1], folder=tmp_path / "none")
    monkeypatch.setitem(liveview.CONFIG, "live", tmp_path / "empty")
    again = liveview.showcase_story()
    assert again["source"] == "bundled" and again["session"] == session
    assert liveview.showcase_story("nope")["session"] == session


def test_no_runs_at_all(monkeypatch, tmp_path):
    monkeypatch.setitem(liveview.CONFIG, "roots", [])
    monkeypatch.setitem(liveview.CONFIG, "live", tmp_path / "empty")
    monkeypatch.setattr(showcase, "BUNDLED", tmp_path / "none")
    assert liveview.showcase_story() == {"found": False, "steps": [], "choices": []}
