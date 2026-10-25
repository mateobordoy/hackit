import contextlib
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from arena_kit.client import ArenaClient
from arena_kit.sim import Sim, create_app, load_cases

from buyer import config, records
from buyer.datapkg import load_package

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    """Every test starts with no learned memory and writes none to the real logs."""
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")


@contextlib.contextmanager
def serve(sim):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(sim), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join()


def practice_like_cases():
    return {c["case_id"]: c for c in (json.loads(p.read_text(encoding="utf-8")) for p in FIXTURES.glob("*.json"))}


@pytest.fixture
def sim_client(monkeypatch, tmp_path):
    """arena-sim with the kit's cases plus our practice-like cases; a frozen clock (no timeouts).
    Run logs go to a temp folder."""
    monkeypatch.setattr(records, "RUNS", tmp_path / "runs")
    sim = Sim({**load_cases(), **practice_like_cases()}, clock=lambda: 1_000_000.0)
    with serve(sim) as url:
        key = ArenaClient(url).register("tests", "sim")["api_key"]
        yield ArenaClient(url, key)


@pytest.fixture(scope="session")
def pkg():
    p = load_package()
    if p is None:
        pytest.skip("no data package (run: buyer data)")
    return p
