"""The whole agent against arena-sim: the API contract, the scheduling, the close. The sim's
supplier is a plain script (not the hosted bots), so this proves plumbing and guards, not tactics."""

import pytest
from arena_kit.cost import must_haves_ok

from buyer import records
from buyer.runner import play_batch

CASES = ["case:S01", "case:S02", "case:S03", "case:X01", "case:X02", "case:X03", "case:X04"]


@pytest.mark.parametrize("case_id", CASES)
def test_every_case_closes_a_valid_deal(sim_client, pkg, case_id):
    case = next(c for c in sim_client.cases("practice") if c["case_id"] == case_id)
    [(cid, result)] = play_batch(sim_client, [case], repeat=1, parallel=1)
    assert not isinstance(result, Exception), result
    assert result["deal"], result
    assert result["must_haves_ok"]
    assert must_haves_ok(case, result["terms"])
    assert result["c_deal"] <= result["c_open"] + 0.01
    assert result["score"] > 0.3, result
    runs = records.load(records.RUNS)
    assert runs and runs[-1]["case"] == case_id
