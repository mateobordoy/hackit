"""The next price, chosen by playing each candidate forward against every hypothesis still alive.

For each candidate price we simulate the supplier's answer under each hypothesis, then our default
continuation (a fixed share of the remaining gap, never a tiny move) until it agrees, finals,
walks or our messages run out, and score the outcome in euros with the case's own cost curve
(the relationship charge included). The candidate with the lowest expected C wins. Accepting its
standing sheet now is one more candidate, allowed once it has shown how it moves.
"""

from dataclasses import dataclass

from . import config
from .belief import AGREED, FINAL, WALK, respond


@dataclass
class Context:
    moves_left: int  # our messages left in this thread, this one included
    cost_of: callable  # normalised price -> C in EUR
    walk_cost: float  # C if the supplier walks away (the alternative, or a no-deal penalty)
    cap: float | None = None  # normalised price ceiling (the alternative's break-even)
    accept_cost: float | None = None  # C of accepting its standing sheet now (None: not acceptable)


@dataclass
class Decision:
    kind: str  # "offer" | "accept"
    price: float | None  # normalised
    expected: float
    table: list  # (price, expected C, P(walk), expected rounds) per candidate


def candidates(st, ctx):
    lo = st.x_prev
    top = st.c_prev * config.MAX_SHARE_OF_COUNTER
    if ctx.cap is not None:
        top = min(top, ctx.cap)
    if top <= lo:
        return []
    out = set()
    for share in config.CANDIDATE_SHARES:
        x = lo + share * (st.c_prev - lo)
        x = max(x, lo * (1 + config.MIN_MOVE))
        out.add(round(min(x, top), 5))
    return sorted(out)


def outcome(p, st, x, ctx):
    """(C, walked, rounds) of offering `x` now, then continuing by default, under hypothesis p."""
    left, rounds = ctx.moves_left, 0
    while True:
        status, c, nxt = respond(p, st, x)
        left, rounds = left - 1, rounds + 1
        if status == AGREED:
            return ctx.cost_of(x), False, rounds
        if status == WALK:
            return ctx.walk_cost, True, rounds
        if status == FINAL or left <= 0:
            return min(ctx.cost_of(c), ctx.walk_cost), False, rounds
        nx = x + config.ROLLOUT_SHARE * (c - x)
        nx = max(nx, x * (1 + config.MIN_MOVE))
        nx = min(nx, c * config.MAX_SHARE_OF_COUNTER)
        if ctx.cap is not None:
            nx = min(nx, ctx.cap)
        if nx <= x:  # nowhere left to go: take what stands
            return min(ctx.cost_of(c), ctx.walk_cost), False, rounds
        x, st = nx, nxt


def decide(belief, st, ctx):
    table = []
    for x in candidates(st, ctx):
        exp_c = p_walk = exp_r = 0.0
        for p, w in zip(belief.particles, belief.weights, strict=True):
            c, walked, rounds = outcome(p, st, x, ctx)
            exp_c += w * c
            p_walk += w * walked
            exp_r += w * rounds
        table.append((x, exp_c, p_walk, exp_r))
    if not table:
        if ctx.accept_cost is not None:
            return Decision("accept", None, ctx.accept_cost, table)
        return Decision("offer", None, ctx.walk_cost, table)
    best = min(table, key=lambda r: (round(r[1], 0), r[3], r[0]))
    # accept now only once it has shown how it moves and going on is worth less than 0.05 % of C:
    # a last offer just under its counter usually lands an agreement there
    if ctx.accept_cost is not None and st.k >= 3 and ctx.accept_cost <= best[1] + config.ACCEPT_MARGIN * abs(best[1]):
        return Decision("accept", None, ctx.accept_cost, table)
    return Decision("offer", best[0], best[1], table)
