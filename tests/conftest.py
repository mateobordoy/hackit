"""Shared test helpers: a local arena simulator, builders for supplier events and case variants, a
scripted language model and a fake data package that only knows the Sunday release."""

import contextlib
import copy
import json
import socket
import threading
import time

import pytest
import uvicorn
from arena_kit.cost import complete_offer
from arena_kit.sim import Sim, create_app, load_cases

from hackit2 import session
from hackit2.prep import alpha


@pytest.fixture(autouse=True)
def private_live_files(tmp_path, monkeypatch):
    """Live telemetry of test cases goes to a temporary folder, never into the real logs/live."""
    monkeypatch.setattr(session, "LIVE_LOGS", tmp_path / "live")
    monkeypatch.setattr(alpha, "_cache", {})


# --- the simulator ----------------------------------------------------------------------------


@contextlib.contextmanager
def running_simulator(sim):
    """Serve `sim` on a free loopback port and yield its base URL."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(sim), port=port, log_level="warning"))
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    while not server.started:
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        worker.join()


@pytest.fixture
def arena_url():
    """The kit's simulator cases behind a frozen clock, so no thread ever hits its 60-second limit."""
    with running_simulator(Sim(load_cases(), clock=lambda: 1_000_000.0)) as url:
        yield url


# --- events and cases -------------------------------------------------------------------------


def supplier_quote(status, terms, offer="offer:S-1"):
    """A one-message thread: the supplier's sheet with the given status."""
    return [{"from": "supplier", "status": status, "term_sheets": [{"term_sheet_id": offer, "terms": terms}]}]


def supplier_offer(sheet_id, terms, status="offer"):
    """A single supplier message carrying one sheet."""
    return {
        "from": "supplier",
        "status": status,
        "body": "Our offer.",
        "term_sheets": [{"term_sheet_id": sheet_id, "terms": terms}],
    }


def s01_with_quote(price=15.0):
    """A private copy of case S01 and a complete sheet at `price` with 60 days' payment."""
    case = copy.deepcopy(load_cases()["case:S01"])
    return case, complete_offer(case, {"unit_price": price, "payment_days": 60})


LEVERAGE_QUOTE = {"unit_price": 15.88, "payment_days": 30}


def leverage_case():
    """S01 as a leverage case with one supplier and payment terms open for negotiation."""
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["category_goal"] = "leverage"
    case["negotiable"] = ["unit_price", "payment_days"]
    case["fixed_terms"].pop("payment_days", None)
    case["term_options"]["payment_days"] = [14, 30, 60]
    return {**case, "suppliers": ["sup:S"]}


# --- a scripted model -------------------------------------------------------------------------

# Every helper's JSON answer in one object, full of values that no code may accept unchecked.
WILD_ANSWERS = {
    # specialists
    "findings": ["It has free machines."],
    "leverage": "ours",
    # lead researcher
    "trades": [
        {"field": "payment_days", "value": 14, "why": "it is short of cash"},
        {"field": "unit_price", "value": 1.0, "why": "cheaper"},
        {"field": "payment_days", "value": 999, "why": "not an option"},
        "junk",
    ],
    "argument_order": ["bogus", "forecast", "should_cost"],
    "tone": "angry",
    "notes": "A small shop that wants steady work.",
    "reason": "test",
    # sparring supplier
    "arguments": ["Our wages rose.", "Material is up 12 %.", 7],
    "weak_spots": ["Our machines stand idle."],
    "wants": ["faster payment"],
    # sparring buyer
    "answers": [{"to": 1, "answer": "Wages are a small share of this part."}, {"to": 9, "answer": "x"}],
    "raise": ["You mentioned free capacity."],
    # reply reader
    "mood": "irritated",
    "tactic": "hold",
    "argument_no": 1,
    "requests": ["volume", "bogus"],
}


def scripted_model(system, user):
    """JSON prompts get `WILD_ANSWERS`; the e-mail writer gets a harmless paragraph."""
    if "JSON only" in system:
        return json.dumps(WILD_ANSWERS)
    return "We value the cooperation with your team."


def failing_model(system, user):
    raise RuntimeError("model down")


# --- a package that only knows the Sunday release ---------------------------------------------


class SundayReleasePackage:
    """A data package whose SQL tables know nothing of the Sunday release; only its ID index does."""

    rows = {
        "doc:new": {"supplier": "sup:S", "date": "2026-10-08", "type": "letter", "title": "Prices"},
        "doc:old": {"supplier": "sup:S", "date": "2026-03-01", "type": "email", "title": "Holiday shutdown"},
        "doc:other": {"supplier": "sup:T", "date": "2026-10-09", "type": "letter", "title": "Prices"},
        "news:new": {"supplier": "sup:S", "date": "2026-10-06", "headline": "Sold to a group"},
    }

    def ids(self, prefix):
        return [ref for ref in self.rows if ref.startswith(prefix)]

    def lookup(self, ref):
        return self.rows.get(ref)

    def document(self, ref):
        return f"text of {ref}"

    def query(self, sql, params=(), limit=None):
        return []
