"""A supplier as a set of hypotheses (particles), narrowed by every reply.

What the logged practice suppliers showed [pc]: each steps down toward a hidden floor, a smaller
step every round; it concedes only after a real move from us; it agrees as soon as our sheet
reaches the counter it would make; it sends a final offer once the cumulative gap we showed
exhausts its patience or after holding at its floor; far beyond its patience it walks.

One hypothesis = (F floor, beta first-round share of the way to F, alpha later share, P patience,
m smallest move of ours it counts as a move, H holds at the floor before its final offer).
Prices are shares of its opening quote at our planned terms (1.0 = its opening price).
"""

import math
import random
from dataclasses import dataclass

from . import config

OPEN, FINAL, AGREED, WALK = "open", "final_offer", "agreed", "walked_away"


@dataclass
class State:
    """What has been observed before our next message (identical for every hypothesis)."""

    k: int  # the round of our next message, 1-based
    c_prev: float  # its standing price
    x_prev: float | None  # our last price, None before our first sheet
    gap_sum: float = 0.0  # cumulative relative gap shown so far
    holds: int = 0  # consecutive counters without a concession


def respond(p, st, x):
    """(status, its price, next state) for hypothesis `p` answering our price `x`."""
    floor, beta, alpha, patience, min_move, holds_max = p
    share = beta if st.k == 1 else alpha
    target = max(floor, st.c_prev - share * (st.c_prev - floor))
    if x >= target - 1e-9:
        return AGREED, x, st
    if st.x_prev is not None and (x - st.x_prev) / st.x_prev < min_move:
        nxt = State(st.k + 1, st.c_prev, x, st.gap_sum + (st.c_prev - x) / st.c_prev, st.holds + 1)
        return FINAL, st.c_prev, nxt
    gap = st.gap_sum + (target - x) / target
    holds = st.holds + 1 if st.c_prev - target < 1e-6 else 0
    nxt = State(st.k + 1, target, x, gap, holds)
    if gap >= patience + config.WALK_EXTRA_GAP:
        return WALK, target, nxt
    if gap >= patience or holds >= holds_max:
        return FINAL, target, nxt
    return OPEN, target, nxt


class Belief:
    def __init__(self, floor_mean, floor_sd, patience=config.PATIENCE_PRIOR, floor_min=0.5, n=None, seed=None):
        self.rng = random.Random(config.SEED if seed is None else seed)
        self.n = n or config.PARTICLES
        self.floor_min = floor_min
        self.ceiling = 1.0  # its floor never sits above a price it offered
        self.particles = [self._draw(floor_mean, floor_sd, patience) for _ in range(self.n)]
        self.weights = [1.0 / self.n] * self.n
        self.updates = 0

    def _draw(self, mean, sd, patience):
        floor = self.rng.gauss(mean, sd)
        floor = min(max(floor, self.floor_min, 0.5), 0.999)
        return [
            floor,
            self.rng.uniform(0.1, 0.9),
            self.rng.uniform(0.1, 0.6),
            self.rng.uniform(*patience),
            self.rng.uniform(0.001, 0.012),
            self.rng.choice((1, 2)),
        ]

    # -- learning ----------------------------------------------------------------------------
    def update(self, st, x, status, c_obs):
        """Weigh every hypothesis by how well it predicts the reply to our price `x`."""
        if status == OPEN or status == FINAL:
            self.ceiling = min(self.ceiling, c_obs)
        elif status == AGREED:
            self.ceiling = min(self.ceiling, x)
        sigma = config.OBS_SIGMA
        miss = (1 - config.STATUS_MATCH) / 3
        for i, p in enumerate(self.particles):
            s_pred, c_pred, _ = respond(p, st, x)
            like = config.STATUS_MATCH if s_pred == status else miss
            if status in (OPEN, FINAL) and c_obs is not None:
                predicted = c_pred if s_pred != AGREED else max(p[0], st.c_prev - p[2] * (st.c_prev - p[0]))
                like *= math.exp(-0.5 * ((c_obs - predicted) / sigma) ** 2) + 1e-12
            if p[0] > self.ceiling + 0.003:
                like *= 1e-6
            self.weights[i] *= like
        self._normalise()
        self.updates += 1
        if self.ess() < self.n / 3:
            self._resample()

    def _normalise(self):
        total = sum(self.weights)
        if total <= 0 or not math.isfinite(total):
            self.weights = [1.0 / self.n] * self.n
        else:
            self.weights = [w / total for w in self.weights]

    def ess(self):
        return 1.0 / sum(w * w for w in self.weights)

    def _resample(self):
        step, u = 1.0 / self.n, self.rng.random() / self.n
        cum, i, out = self.weights[0], 0, []
        for _ in range(self.n):
            while u > cum and i < self.n - 1:
                i += 1
                cum += self.weights[i]
            out.append(list(self.particles[i]))
            u += step
        for p in out:  # jitter: keep the cloud alive around what fits
            p[0] = min(max(p[0] + self.rng.gauss(0, 0.004), self.floor_min, 0.5), self.ceiling, 0.999)
            p[1] = min(max(p[1] + self.rng.gauss(0, 0.03), 0.02), 0.98)
            p[2] = min(max(p[2] + self.rng.gauss(0, 0.03), 0.02), 0.95)
            p[3] = max(p[3] + self.rng.gauss(0, 0.02), 0.03)
            p[4] = min(max(p[4] + self.rng.gauss(0, 0.001), 0.0), 0.03)
        self.particles, self.weights = out, [1.0 / self.n] * self.n

    # -- reading it --------------------------------------------------------------------------
    def quantile(self, q, idx=0):
        pairs = sorted((p[idx], w) for p, w in zip(self.particles, self.weights, strict=True))
        acc = 0.0
        for v, w in pairs:
            acc += w
            if acc >= q:
                return v
        return pairs[-1][0]

    def mean(self, idx=0):
        return sum(p[idx] * w for p, w in zip(self.particles, self.weights, strict=True))

    def summary(self):
        return {
            "floor_p10": round(self.quantile(0.1), 4),
            "floor_p50": round(self.quantile(0.5), 4),
            "floor_p90": round(self.quantile(0.9), 4),
            "patience": round(self.mean(3), 3),
            "alpha": round(self.mean(2), 3),
            "ess": round(self.ess(), 1),
        }
