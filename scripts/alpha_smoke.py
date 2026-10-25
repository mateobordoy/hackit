"""Exercise nine-agent preparation and full-cost telemetry without hosted quota or model spend.

This is a labelled synthetic demonstration, not a measurement of live LLM negotiation quality.
"""

import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path

import uvicorn
from arena_kit import ArenaClient, RemoteTable
from arena_kit.sim import Sim, create_app, load_cases

from hackit2 import session


def main():
    cases = load_cases()
    case = cases["case:S01"]
    case["title"] = "Alpha local demo: scripted LLM and synthetic evidence"
    sid = case["suppliers"][0]

    class SyntheticPackage:
        rows = {
            "doc:alpha-note": {"supplier": sid, "date": "2026-10-01", "title": "Synthetic payment note"},
            "news:alpha-demo": {"supplier": sid, "date": "2026-10-02", "headline": "Synthetic capacity note"},
        }

        def ids(self, prefix):
            return [ref for ref in self.rows if ref.startswith(prefix)]

        def lookup(self, ref):
            return self.rows.get(ref)

        def document(self, ref):
            return "SYNTHETIC DEMO: The supplier values earlier payment and a stable relationship."

        def query(self, sql, params=(), limit=None):
            return []

    def scripted(system, user):
        if "You are Scout" in system:
            state = json.loads(user)
            if not state["observations"]:
                return json.dumps({"requests": [{"tool": "search", "args": {"query": "earlier payment"}}]})
            ref = state["observations"][0]["source_id"]
            return json.dumps(
                {
                    "findings": [
                        {
                            "claim": "Synthetic evidence mentions earlier payment.",
                            "source_ids": [ref],
                            "status": "observed",
                            "confidence": "high",
                            "decision_use": "Evaluate a payment alternative.",
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
                            "claim": "The synthetic note describes a payment preference.",
                            "source_ids": [sources[0]["source_id"]],
                            "status": "observed",
                            "confidence": "high",
                        }
                    ]
                    if sources
                    else []
                }
            )
        if "lead researcher" in system:
            return json.dumps(
                {
                    "trades": [],
                    "argument_order": [],
                    "tone": "warm",
                    "notes": "This is a local scripted demonstration; the evidence is synthetic.",
                    "reason": "Validate orchestration and cost visibility, not performance.",
                }
            )
        if "JSON only" in system:
            return '{"mood":"neutral","tactic":"steady","requests":[]}'
        return "Thank you for considering our proposal. We appreciate the continued cooperation."

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(Sim(cases, clock=lambda: 1_000_000.0)), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    while not server.started:
        time.sleep(0.01)
    os.environ["HACKIT_RESEARCH_MODE"] = "alpha"
    os.environ["HACKIT_BRIEF_CACHE"] = str(Path("logs/briefs/local-alpha-smoke"))
    try:
        url = f"http://127.0.0.1:{port}"
        key = ArenaClient(url).register(f"alpha-local-{secrets.token_hex(3)}", "sim")["api_key"]
        client = ArenaClient(url, key, rate=1000)
        table = RemoteTable(client, case)
        result = session.run_case(table, case["case_id"], SyntheticPackage(), complete=scripted, learn=False)
        print(
            json.dumps(
                {
                    "validation": "LOCAL SIMULATOR + SCRIPTED LLM + SYNTHETIC EVIDENCE",
                    "deal": result.get("deal"),
                    "total_cost": result.get("c_deal"),
                    "hosted_sessions": 0,
                    "model_api_calls": 0,
                    "watch": "http://127.0.0.1:8793",
                },
                indent=2,
            )
        )
    finally:
        server.should_exit = True
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
