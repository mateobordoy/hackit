"""Discovery must retrieve real evidence, respect deadlines and feed a legal strategy to the engine."""

import json
import sqlite3
import time

import pytest
from conftest import SundayReleasePackage, leverage_case

from hackit2 import lessons, settings, telemetry
from hackit2.prep import alpha, briefing
from hackit2.prep.alpha_roles import EXPERTS
from hackit2.prep.research_tools import ResearchTools


def discovery_model(calls):
    def complete(system, user):
        calls.append((system, user))
        if "You are Scout" in system:
            state = json.loads(user)
            if not state["observations"]:
                return json.dumps({"requests": [{"tool": "search", "args": {"query": "Sold group"}}]})
            ref = state["observations"][0]["source_id"]
            return json.dumps(
                {
                    "findings": [
                        {
                            "claim": "A company ownership change is reported.",
                            "source_ids": [ref],
                            "status": "observed",
                            "confidence": "medium",
                        }
                    ]
                }
            )
        if "industrial procurement specialist" in system:
            sources = json.loads(user)["sources"]
            return json.dumps(
                {
                    "findings": [
                        {
                            "claim": "A supplier document is available.",
                            "source_ids": [sources[0]["source_id"]],
                            "status": "observed",
                        }
                    ]
                    if sources
                    else []
                }
            )
        return json.dumps(
            {
                "trades": [{"field": "payment_days", "value": 14}, {"field": "unit_price", "value": 1}],
                "argument_order": ["notice"],
                "tone": "warm",
                "notes": "Consider the documented change.",
            }
        )

    return complete


def test_nine_agents_discover_sources_and_feed_the_synthesis_lead(monkeypatch):
    monkeypatch.setattr(lessons, "recall_lessons", lambda case: [])
    calls = []
    result = alpha.prepare(leverage_case(), SundayReleasePackage(), discovery_model(calls), budget_s=5)
    brief = result["sup:S"]
    assert set(brief["research"]["reports"]) == set(EXPERTS) | {"scout_discovery"}
    assert len([system for system, _ in calls if "industrial procurement specialist" in system]) == 8
    assert len(brief["research"]["tool_calls"]) == 1
    assert brief["research"]["reports"]["scout_discovery"]["evidence"]
    assert brief["trades"] == [{"field": "payment_days", "value": 14, "why": ""}]
    lead_input = json.loads(calls[-1][1])
    assert "scout_discovery" in lead_input["reports"]
    assert "unit_price" not in [t["field"] for t in brief["trades"]]


def test_unknown_citations_and_unstructured_findings_cannot_become_evidence():
    raw = {
        "findings": [
            "unsupported prose",
            {"claim": "invented", "source_ids": ["source:999"]},
            {"claim": "possible", "source_ids": ["source:1"], "status": "guess"},
        ]
    }
    report = alpha.clean_report(raw, {"source:1": {}})
    assert report["findings"] == ["possible"]
    assert report["evidence"][0]["status"] == "inferred"
    assert report["evidence"][0]["confidence"] == "low"


def test_synthesis_can_use_the_remaining_budget_instead_of_the_legacy_stage_cap(monkeypatch):
    monkeypatch.setattr(settings, "PREP_STAGE_S", 0.005)

    def complete(system, user):
        if "lead researcher" in system:
            time.sleep(0.03)
            return '{"tone":"warm"}'
        return "{}"

    assert alpha.prepare(leverage_case(), None, complete, budget_s=1)["sup:S"]["tone"] == "warm"


@pytest.fixture
def sql_tools(tmp_path):
    path = tmp_path / "research.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE notes (id TEXT, body TEXT)")
    db.execute("INSERT INTO notes VALUES ('doc:one', 'Capacity is available')")
    db.commit()
    package = type("Package", (), {"db": db})()
    tools = ResearchTools(package, time.monotonic() + 2, "sup:S")
    yield tools
    db.close()


def test_sql_is_read_only_and_expensive_queries_are_interrupted(sql_tools, monkeypatch):
    assert sql_tools.query("SELECT * FROM notes")[0]["id"] == "doc:one"
    for sql in (
        "DELETE FROM notes",
        "ATTACH DATABASE ':memory:' AS other",
        "",
        "WITH x AS (SELECT 1) DELETE FROM notes",
    ):
        with pytest.raises((ValueError, sqlite3.DatabaseError)):
            sql_tools.query(sql)
    monkeypatch.setattr(settings, "ALPHA_SQL_TIMEOUT_S", 0.01)
    started = time.monotonic()
    with pytest.raises(sqlite3.DatabaseError):
        sql_tools.query("WITH RECURSIVE c(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM c) SELECT sum(x) FROM c")
    assert time.monotonic() - started < 0.5
    assert sql_tools.query("SELECT count(*) AS n FROM notes")[0]["n"] == 1


def test_real_package_database_discovery_never_queries_the_shared_connection(sql_tools):
    from pathlib import Path

    class BusyConnection:
        def execute(self, *args):
            raise AssertionError("shared database connection must not be queried for discovery")

    package = type("Package", (), {"root": Path(sql_tools.db_path).parent, "db": BusyConnection()})()
    tools = ResearchTools(package, time.monotonic() + 1, "sup:S")
    assert tools.query("SELECT count(*) AS n FROM notes")[0]["n"] == 1


def test_schema_discovery_uses_its_own_connection_and_honors_deadline(sql_tools):
    def shared_tables():
        raise AssertionError("schema must not issue PRAGMA on the shared connection")

    sql_tools.pkg.tables = shared_tables
    assert sql_tools.schema() == {"notes": {"columns": ["id TEXT", "body TEXT"], "rows": 1}}
    sql_tools.deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError):
        sql_tools.schema()


def test_synthesis_has_one_complete_json_contract_including_the_playbook():
    calls = []
    alpha.prepare(leverage_case(), SundayReleasePackage(), discovery_model(calls), budget_s=5)
    prompt = next(system for system, _ in calls if "lead researcher" in system)
    contract = json.loads(prompt.split("COMPLETE JSON EXAMPLE:\n", 1)[1])
    assert {"trades", "argument_order", "tone", "notes", "reason", "arguments", "answers", "raise", "wants"} == set(
        contract
    )
    assert contract["answers"][0]["to"] == 1


def test_discovery_rejects_paths_deduplicates_lookups_and_enforces_budget(monkeypatch):
    tools = ResearchTools(SundayReleasePackage(), time.monotonic() + 2, "sup:S")
    assert "error" in tools.call({"tool": "document", "args": {"ref": "/tmp/.env"}})
    request = {"tool": "search", "args": {"query": "Sold group"}}
    first = tools.call(request)
    assert first == tools.call(request) and len(tools.calls) == 2
    monkeypatch.setattr(settings, "ALPHA_TOOL_CALLS", 2)
    assert "budget" in tools.call({"tool": "schema"})["error"]


def test_seeds_cover_all_parts_and_keep_release_sources():
    package = SundayReleasePackage()
    queried_parts = []

    def query(sql, params=(), limit=None):
        if "WHERE part_id" in sql:
            queried_parts.append(params[0])
        return []

    package.query = query
    case = {**leverage_case(), "parts": ["part:A", "part:B"]}
    tools = ResearchTools(package, time.monotonic() + 2, "sup:S")
    alpha.seed_sources(tools, case, "sup:S")
    assert set(queried_parts) == {"part:A", "part:B"}
    assert any(source.get("ref") == "news:new" for source in tools.sources.values())


def test_alpha_cache_is_separate_from_baseline_and_reused_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv(briefing.BRIEF_CACHE_ENV, str(tmp_path))
    calls = []
    case = leverage_case()
    brief = alpha.prepare(case, SundayReleasePackage(), discovery_model(calls), budget_s=5)
    assert alpha.cache_path(case).is_file() and alpha.VERSION in str(alpha.cache_path(case))
    assert not (tmp_path / "S01.json").exists()
    monkeypatch.setattr(alpha, "_cache", {})
    assert alpha.prepare(case, None, lambda *_: pytest.fail("cache should avoid model calls")) == brief


def test_slow_research_cannot_hold_up_the_case(tmp_path):
    def slow(system, user):
        time.sleep(0.2)
        return "{}"

    started = time.monotonic()
    assert alpha.prepare(leverage_case(), None, slow, budget_s=0.04) == {}
    assert time.monotonic() - started < 0.15


def test_alpha_telemetry_records_tool_requests_and_retrieved_source(tmp_path):
    path = tmp_path / "research.jsonl"
    token = telemetry.bind(path)
    try:
        alpha.prepare(leverage_case(), SundayReleasePackage(), discovery_model([]), budget_s=5)
    finally:
        telemetry.unbind(token)
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    lookup = next(e for e in entries if e["event"] == "research_tool")
    assert lookup["request"]["tool"] == "search" and lookup["result"]["source_id"]
    assert any(e["event"] == "prep_stage" and e.get("agents") == 9 for e in entries)
