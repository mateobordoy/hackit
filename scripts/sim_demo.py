"""Play a few arena-sim cases locally (no arena, no quota) so `buyer gui` has runs to show.

python scripts/sim_demo.py X01 X02 S01
"""

import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "tests"))

from arena_kit.client import ArenaClient  # noqa: E402
from arena_kit.sim import Sim, load_cases  # noqa: E402
from conftest import practice_like_cases, serve  # noqa: E402

from buyer import memory  # noqa: E402
from buyer.runner import play_batch  # noqa: E402

# sim suppliers are not the arena's: their stops must never shift the real practice or final priors
memory.path = lambda set_: memory.config.LOG_DIR / f"memory_sim_{set_ or 'unknown'}.jsonl"


def main(ids):
    ids = [i if i.startswith("case:") else f"case:{i}" for i in ids or ["X01", "X02", "S01"]]
    sim = Sim({**load_cases(), **practice_like_cases()}, clock=time.time)
    with serve(sim) as url:
        client = ArenaClient(url, ArenaClient(url).register("demo", "sim")["api_key"])
        cases = [c for c in client.cases("practice") if c["case_id"] in ids]
        for cid, result in play_batch(client, cases, repeat=1, parallel=3):
            print(cid, {k: result.get(k) for k in ("deal", "score", "c_deal")} if isinstance(result, dict) else result)


if __name__ == "__main__":
    main(sys.argv[1:])
