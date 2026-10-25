"""The Live Final loop on arena-sim's rehearsal: waves appear, every case is played inside its cap,
the loop returns after the last wave. Real clock: takes about a minute."""

import time

import pytest
from arena_kit.client import ArenaClient
from arena_kit.sim import Sim, load_cases

from buyer import config, records
from buyer.runner import play_waves
from tests.conftest import serve


@pytest.mark.slow
def test_play_waves_plays_every_case_of_both_waves(monkeypatch, tmp_path, pkg):
    monkeypatch.setattr(records, "RUNS", tmp_path / "runs")
    # the real cap is 8 minutes and we close 90 s before it; the rehearsal here is 25 s per wave
    monkeypatch.setattr(config, "CLOSE_BEFORE_CAP_S", 6)
    monkeypatch.setattr(config, "ROUND_MARGIN_S", 2)
    sim = Sim(load_cases())
    sim.rehearse(wait=2, cap=25, brk=2)
    with serve(sim) as url:
        client = ArenaClient(url, ArenaClient(url).register("final", "sim")["api_key"])
        t0 = time.time()
        out = play_waves(client, waves=2, parallel=10, poll=1.0)
        took = time.time() - t0
    assert len(out) == 2 * len(load_cases())
    for cid, result in out.items():
        assert not isinstance(result, Exception), (cid, result)
        assert result.get("deal"), (cid, result)
        assert result.get("must_haves_ok")
    assert took < 2 + 25 + 2 + 25 + 15
