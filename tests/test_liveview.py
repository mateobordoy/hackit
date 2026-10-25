"""The watch UI: a sim case writes its live file, the UI serves it as the contract says
(docs/liveview-contract.md), and telemetry never changes a decision."""

import json
import threading
import urllib.request

from buyer import config, liveview, telemetry
from buyer.runner import play_batch


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read()) if path.startswith("/api") else r.read().decode()


def test_sim_case_is_watchable(sim_client, pkg, monkeypatch):
    case = next(c for c in sim_client.cases("practice") if c["case_id"] == "case:X01")
    [(_, result)] = play_batch(sim_client, [case], repeat=1, parallel=1)
    assert result["deal"], result
    path = config.LOG_DIR / "live" / "case_X01.jsonl"
    kinds = [json.loads(line)["event"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert kinds[0] == "case_start" and kinds[-1] == "end"
    assert {"message_in", "message_out", "note"} <= set(kinds)

    monkeypatch.setitem(liveview.CONFIG, "roots", [])
    server = liveview.serve(0, live=config.LOG_DIR / "live", roots=[], offline=True, background=True)
    port = server.server_address[1]
    try:
        cases = get(port, "/api/cases")
        assert cases[0]["case"] == "X01" and cases[0]["status"] == "done"
        out = get(port, "/api/events?case=X01&start=0")
        assert out["next"] == len(kinds)
        notes = [e for e in out["events"] if e["event"] == "note"]
        assert any(n["what"] == "setup" and n.get("q0") for n in notes)
        modules = {m for e in out["events"] for t in e["transfers"] for m in (t["from"], t["to"])}
        assert {"arena", "runner", "policy", "belief", "planner", "writer"} <= modules
        assert get(port, f"/api/events?case=X01&start={out['next']}")["events"] == []
        assert "<html" in get(port, "/").lower()
        assert get(port, "/api/scores")["rows"]
        session = cases[0]["session"]
        assert get(port, f"/api/run?session={session}")["found"]
    finally:
        server.shutdown()


def test_no_live_writes_nothing(monkeypatch):
    monkeypatch.setenv("BUYER_LIVE", "0")
    assert telemetry.bind("case:P01") is None
    telemetry.emit("note", what="x")
    assert not (config.LOG_DIR / "live").exists()


def test_emit_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry._local, "path", tmp_path / "missing" / "x.jsonl", raising=False)
    telemetry.emit("note", what="x", value=threading.Lock())  # unwritable folder, odd value
    telemetry.unbind()
