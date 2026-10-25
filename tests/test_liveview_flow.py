"""The connection inspector displays recorded evidence, including calls across model threads."""

import copy
import json
import shutil
import subprocess

import pytest

from hackit2 import liveview


def test_scout_model_and_data_exchanges_keep_their_actual_provenance():
    events = [
        {"event": "model_call", "at": 1, "call": 99, "role": "researcher:scout_discovery", "user": "seed sources"},
        {
            "event": "research_tool",
            "at": 2,
            "supplier": "sup:a",
            "tool": "sql",
            "request": {"tool": "sql", "args": {"query": "SELECT * FROM notes"}},
            "result": {"source_id": "source:4", "data": "retrieved rows", "truncated": False},
        },
        {
            "event": "prep_report",
            "at": 3,
            "supplier": "sup:a",
            "specialist": "scout_discovery",
            "report": {"findings": ["sourced observation"]},
        },
    ]
    output = liveview.flow_events(events)
    assert output[0]["transfers"][0]["from"] == "research.scout"
    lookup = output[1]["transfers"][0]
    assert (lookup["from"], lookup["to"]) == ("research.scout", "data")
    assert lookup["request"] == events[1]["request"] and lookup["response"] == events[1]["result"]
    report = output[2]["transfers"][0]
    assert (report["from"], report["to"]) == ("research.scout", "prep.briefing")
    assert report["request"] == events[2]["report"]


def test_brand_and_research_counts_describe_alpha_not_the_beta_buyer_name():
    html = liveview.PAGE_FILE.read_text()
    assert "<title>hackit2 Live" in html and "<h1>hackit2 Live" in html
    assert "four analysts" not in html and "8 experts + Scout" in html


def trace(phase, **values):
    return {
        "event": "trace",
        "phase": phase,
        "fn": "pricing.price_ceiling",
        "call": 7,
        "thread": "buyer",
        "at": 10,
        **values,
    }


def test_trace_return_retains_its_actual_request_without_mutating_the_journal():
    events = [
        trace("start", parent="negotiation.engine.next_message", args={"supplier": "sup:a", "arg2": 15}),
        trace("end", at=11, result=16.2),
    ]
    original = copy.deepcopy(events)
    enriched = liveview.flow_events(events)
    first, last = (event["transfers"][0] for event in enriched)
    assert first["id"] == last["id"]
    assert not first["complete"] and last["complete"]
    assert last["request"] == {"supplier": "sup:a", "arg2": 15}
    assert last["response"] == 16.2
    assert (last["from"], last["to"], last["supplier"]) == ("negotiation.engine", "pricing", "sup:a")
    assert last["summary"]
    assert events == original


def test_missing_parent_does_not_invent_a_session_caller():
    assert "transfers" not in liveview.flow_events([trace("end", result=16)])[0]


def test_trace_error_is_shown_as_an_error_result():
    events = [
        trace("start", parent="negotiation.engine.next_message", args={}),
        trace("error", at=11, error="ValueError('bad data')"),
    ]
    assert liveview.flow_events(events)[1]["transfers"][0]["response"] == {"error": "ValueError('bad data')"}


def test_model_answer_matches_caller_across_different_worker_threads():
    events = [
        {
            "event": "model_call",
            "at": 1,
            "thread": "buyer",
            "call": 10,
            "role": "writer",
            "supplier": "sup:a",
            "system": "Write a reply",
            "user": "Seller email",
        },
        {
            "event": "model_done",
            "at": 2,
            "thread": "model_0",
            "call": 10,
            "role": "writer",
            "status": "ok",
            "answer": "Thank you.",
            "seconds": 1,
        },
    ]
    first, last = (event["transfers"][0] for event in liveview.flow_events(events))
    assert first["id"] == last["id"]
    assert last["request"]["user"] == "Seller email"
    assert last["response"]["answer"] == "Thank you."
    assert last["from"] == "negotiation.engine" and last["supplier"] == "sup:a"


def test_late_model_answer_does_not_erase_the_callers_fallback_decision():
    events = [
        {"event": "model_call", "at": 1, "call": 10, "role": "writer", "user": "Reply"},
        {"event": "model_outcome", "at": 2, "call": 10, "status": "timeout"},
        {"event": "model_done", "at": 4, "call": 10, "status": "ok", "answer": "Late reply"},
    ]
    row = liveview.flow_events(events)[-1]["transfers"][0]
    assert row["complete"] and row["response"]["answer"] == "Late reply"
    assert row["outcome"] == "timeout"


def test_data_connection_uses_recorded_analyst_input_and_excludes_backoffice():
    events = [
        {
            "event": "model_call",
            "at": 1,
            "call": 10,
            "role": "specialist:persona",
            "supplier": "sup:a",
            "user": "NEWS: supplied excerpt",
        },
        {"event": "supplier_note", "at": 2, "text": "Private seller reasoning"},
    ]
    enriched = liveview.flow_events(events)
    data = next(row for row in enriched[0]["transfers"] if row["from"] == "data")
    assert data["request"] == {"recorded_analyst_input": "NEWS: supplied excerpt"}
    assert "transfers" not in enriched[1]


def test_incremental_events_can_inspect_a_call_started_before_the_cursor(tmp_path, monkeypatch):
    events = [trace("start", parent="negotiation.engine.next_message", args={"arg2": 15}), trace("end", result=16)]
    path = tmp_path / "case_P01.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events))
    monkeypatch.setattr(liveview, "live_files", lambda: {"P01": (path, "test")})
    result = liveview.case_events("P01", 1)
    assert len(result["events"]) == 1
    assert result["events"][0]["transfers"][0]["request"] == {"arg2": 15}
    assert result["next"] == 2


def test_data_lookup_is_explicitly_a_summary_and_cached_brief_is_identified():
    events = [
        trace(
            "start",
            fn="prep.briefing.data_slice",
            parent="prep.briefing.prepare_briefs",
            args={"arg2": "sup:a", "arg3": "persona"},
        ),
        trace("end", fn="prep.briefing.data_slice", result="231 chars of data"),
        {"event": "prep_brief", "at": 15, "frozen": True, "supplier": "sup:a", "brief": {"tone": "warm"}},
    ]
    enriched = liveview.flow_events(events)
    row = enriched[1]["transfers"][0]
    assert row["summary"] and row["response"] == "231 chars of data"
    assert row["request"] == {"supplier": "sup:a", "analyst": "persona"}
    assert "cached" in enriched[2]["transfers"][0]["label"]


@pytest.mark.skipif(not shutil.which("node"), reason="Node is used only to validate the browser JavaScript")
def test_ui_inspector_handles_replay_pending_completion_and_untrusted_text():
    script = liveview.PAGE_FILE.read_text().split("<script>", 1)[1].split("</script>", 1)[0]
    events = liveview.flow_events(
        [
            {
                "event": "model_call",
                "at": 1,
                "thread": "buyer",
                "call": 10,
                "role": "writer",
                "system": "Write",
                "user": "<script>untrusted</script>",
            },
            {
                "event": "model_done",
                "at": 2,
                "thread": "model_0",
                "call": 10,
                "role": "writer",
                "status": "ok",
                "answer": "Thanks",
                "seconds": 1,
            },
        ]
    )
    check = """
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
vm.runInThisContext(data.script);
S=fresh(); apply(data.events[0],false); FLOW=edgeKey('negotiation.engine','model');
assert.match(flowHTML(),/Awaiting a result/);
assert.match(flowHTML(),/&lt;script&gt;untrusted/);
assert.ok(!flowHTML().includes('<script>untrusted'));
apply(data.events[1],false);
assert.equal(S.links[FLOW].length,1);
assert.match(flowHTML(),/Thanks/);
assert.ok(!flowHTML().includes('Awaiting a result'));
assert.match(mapSVG(),/BUYER/); assert.match(mapSVG(),/Seller/); assert.match(mapSVG(),/>Data</);
assert.match(mapSVG(),/role="button" tabindex="0"/);
const elements={};
globalThis.document={activeElement:null,getElementById:id=>elements[id]??=(
 {innerHTML:'',scrollTop:0,contains:()=>false,scrollIntoView:()=>{},querySelectorAll:()=>[]})};
selectFlow('negotiation.engine','model');
assert.match(elements.flowdetail.innerHTML,/Thanks/);
selectNode('data');
assert.match(elements.flowdetail.innerHTML,/Local arena data package/);
assert.match(elements.flowdetail.innerHTML,/selectFlow/);
MAP_NODE=null;
FLOW=edgeKey('data','evidence'); assert.match(flowHTML(),/No exchange was captured/);
S=fresh(); assert.match(flowHTML(),/No exchange was captured/);
"""
    subprocess.run(
        [shutil.which("node"), "-e", check],
        input=json.dumps({"script": script, "events": events}),
        text=True,
        capture_output=True,
        check=True,
    )
