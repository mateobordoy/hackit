"""Every tunable number in one place, each with the evidence it rests on.

Evidence tags: [pc] the ProcureCabana repo (winner of the Munich 2026 jury prize: docs/learnings.md
and its ~170 logged practice threads), [kit] the arena brief / api.md / arena_kit, [data] the
data package. Numbers that are pure engineering limits are marked [eng].
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=None):
    """KEY=VALUE lines from .env into the environment; real environment variables win."""
    p = Path(path) if path else ROOT / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        value = value.strip().strip("\"'")
        if sep and value and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value)


def data_dir():
    return Path(os.environ.get("ARENA_DATA") or ROOT / "data" / "package")


LOG_DIR = ROOT / "logs"

# --- Opening anchor ------------------------------------------------------------------------------
# Opening price as a share of the supplier's quote at our planned terms, by category goal. [pc]
# Practice C_best sat 12-22 % below the quote in leverage/routine/secure-supply cases and 1.5-3 %
# in stabilise ones; these anchors closed 0.78-0.83 practice means. Deeper anchors did not lower
# the first counter and only spent patience (a walk at 39 % cumulative gap, P05). [pc]
ANCHOR = {"leverage": 0.78, "routine": 0.82, "secure_supply": 0.88, "quality_first": 0.88, "stabilise": 0.92}
ANCHOR_DEFAULT = 0.85
ANCHOR_MIN = 0.75  # below this an opening reads as an insult [pc]
ANCHOR_PRICE_FROZEN = 0.95  # "head office froze price reductions": a deep cut only costs patience [pc]
ANCHOR_CAPACITY_FULL = 0.93  # fully booked suppliers final early (Ravensteiner at 15 % gap) [pc]
ANCHOR_NEAR_COST = 0.97  # quote within 2 % of the own-plant should-cost [pc]
NEAR_COST_RATIO = 0.98

# --- Supplier floor priors (share of its opening quote at our terms where it stops) --------------
# Median final price / opening quote over the logged practice threads, by case type. [pc]
# raw_material 0.85-0.88, rfq 0.78-0.81 (job shops) but 1.0 for some bidders, renewal 0.98,
# capacity 0.98, quality 0.97, stabilise price increase 0.93-0.94. sd keeps the prior honest:
# the belief narrows it from the supplier's own counters within two or three replies.
FLOOR_PRIOR = {
    "rfq": (0.84, 0.07),
    "raw_material": (0.88, 0.04),
    "bundling": (0.91, 0.04),
    "price_increase": (0.94, 0.03),
    "renewal": (0.975, 0.02),
    "capacity": (0.975, 0.02),
    "quality": (0.965, 0.02),
}
FLOOR_PRIOR_DEFAULT = (0.92, 0.05)

# Patience: the cumulative relative gap (sum over rounds of (counter - our sheet) / counter) a
# supplier tolerates before its final offer. Typical 36-42 %; relationship-goal, near-cost and
# fully booked suppliers ended at 14-19 %. [pc] Used as a prior range, never as a fixed number.
PATIENCE_PRIOR = (0.30, 0.50)
PATIENCE_PRIOR_TOUCHY = (0.12, 0.30)

# --- Pace ----------------------------------------------------------------------------------------
# A move under ~1 % of the price (a few cents) triggered an early final offer at a worse price
# (P08, P18: final raised 5.43 -> 5.51; P05: "you only moved a few cents" then a walk). [pc]
MIN_MOVE = 0.008
# A 55 % move got a hold and a higher price; 40 % gave the same as 30 %. [pc]
MAX_GAP_SHARE = 0.6
# Never offer at the supplier's own counter: offering it is the same as accepting it, and the
# accept path is explicit. [eng]
MAX_SHARE_OF_COUNTER = 0.998
# A first price drop this large (share of its price, without a term change) means a deep floor:
# hold the anchor; the job shop then agreed at the held anchor (P05 0.828). [pc]
DEEP_FIRST_DROP = 0.06
# The default continuation inside the planner's rollouts. [pc] best fixed share in A/B tests.
ROLLOUT_SHARE = 0.3

# --- Belief / planner ----------------------------------------------------------------------------
PARTICLES = 400  # [eng] 400 x 12 candidates x 9 rounds runs in ~0.1 s
CANDIDATE_SHARES = (0.12, 0.18, 0.25, 0.32, 0.4, 0.5, 0.6)
OBS_SIGMA = 0.008  # counter noise as a share of the opening price; repeat runs differ 1-2 % [pc]
STATUS_MATCH = 0.85  # likelihood that the model predicts the status the supplier really sent
# Beyond its patience by this much (a range: each hypothesis draws its own), a supplier walks
# instead of finalling. Nordmark walked without a final offer at 19 % (P02 v1, and in our own P02
# run at 14 %); others finalled at 36-42 % and walked at 39 % (P05). [pc]
WALK_EXTRA_GAP = (0.0, 0.15)
SEED = 7  # deterministic particles: identical inputs give identical moves [eng]
# Accept its standing sheet before its final word only when going on is worth less than this share
# of C (the model does not know that a token move can make a supplier raise its final). [pc]
ACCEPT_MARGIN = 0.0005

# --- Terms ---------------------------------------------------------------------------------------
# Cost of money per year by owner type, as seen in practice (groups 4-7 %, family 7-8.5 %,
# founder-run job shops 12-17 %); a cash need adds to it. [pc] Learned online from the supplier's
# own priced alternatives when it sends them (see supplier.learn_rate).
OWNER_RATE = {"group": 0.05, "private": 0.07, "family": 0.075, "founder": 0.12}
DEFAULT_RATE = 0.07
CASH_NEED_BONUS = 0.05
RATE_BOUNDS = (0.0, 0.6)
# Overtime a faster delivery costs the supplier, share of price per day: full shops charged
# +4.8 % for 10 days faster (Hrvelec), the others barely anything. [pc]
OVERTIME_FULL, OVERTIME_DEFAULT = 0.005, 0.001
# The most a supplier values an index clause at, share of price (one supplier valued its clause
# at 8.5 %, Nordmark P02). [pc]
CLAUSE_VALUE_MAX = 0.08
CLAUSE_MIN_GAIN = 0.02
# Switch to a package the supplier quoted only when it is clearly cheaper: a 0.1 % threshold let a
# transient spread lock in a looser ppm commitment that cost money (P11). [pc]
ADOPT_MIN_GAIN = 0.003
# True full cost / should-cost: P01 129.5-130.7 vs 132.64, P02 367.4-371.7 vs 374.95, P03
# 394.7-399.4 vs 403.21, solved from the relationship charge of practice deals. [pc]
SHOULD_COST_BIAS = 0.985
EXTRA_SHEETS_MAX = 2  # arena max 3 sheets per message [kit]

# --- Evidence ------------------------------------------------------------------------------------
# Strength = how often the supplier "took it into account" in the logged practice threads. [pc]
# volume_change (forecast) 84/97, rival quote 34/41, index fall 50/60, should-cost 26/37,
# delivery record 24/68, price history (our order prices) 0/100 -> never cited.
CLAIMS_FIRST_ROUND = 3  # the arena checks at most 3 per message [kit]
CLAIMS_LATER = 1
STRENGTH = {
    "rival_offer": 9.5,
    "forecast": 9.0,
    "rival_quote": 8.5,
    "index": 7.0,  # + up to 2 for a large fall
    "should_cost": 7.0,
    "notice": 6.5,
    "quality_record": 5.0,
    "delivery_record": 3.0,
}
MIN_INDEX_FALL_PCT = 0.5

# --- Clocks --------------------------------------------------------------------------------------
CLOCK_S = 60  # per message, from the supplier's reply [kit]
MARGIN_S = 6  # kept on every clock for building and delivering the message [eng]
KEEPALIVE_AFTER_S = 35  # a settled bidder gets a short question after this long [pc]
FIRST_SEND_S = 12  # what a supplier reply takes until one is measured (19 s median, 28 s p90) [pc]
CLOSE_BEFORE_CAP_S = 90  # the kit's own margin before a wave cap [kit]
ROUND_MARGIN_S = 15
CLOSE_TRIES = 3
RETRY_S, RETRY_MARGIN_S, RETRY_MAX = 5, 120, 40  # a crashed final case is resumed [pc]

# --- Model (optional; the agent runs fully without it) -------------------------------------------
LLM_TIMEOUT_S = 20  # reader (READER_REPLY_S) and writer share it per move [eng]
LLM_MIN_S = 3
LLM_MAX_CHARS = 900

# --- Reader agent (optional; on with LLM_MODEL and the Anthropic provider) -----------------------
READER_TIMEOUT_S = 20  # one call, client side [eng]
READER_SETUP_S = 10  # wait at the case start, all suppliers read in parallel [eng]
READER_REPLY_S = 8  # wait per supplier reply, taken from the writer's budget [eng]
READER_MAX_CHARS = 12000

# --- Learning between cases (buyer/memory.py) ----------------------------------------------------
# Pseudo-count of the factory prior: with N0 observed stops the set weighs as much as practice. [eng]
MEMORY_N0 = 4
# The most the learned population may move a floor prior, as a share of the quote: practice type
# means sat 0.84 to 0.975, a few points either way is a different population, more is noise. [eng]
MEMORY_MAX_SHIFT = 0.03
MEMORY_MAX_SD = 0.08
# Never lower: a greedier prior cost P18 0.07 (memory.py). [pc]
MEMORY_MAX_DOWN = 0.0
