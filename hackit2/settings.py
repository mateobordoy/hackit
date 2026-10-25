"""Every tunable number of the agent in one place.

Modules read these as `settings.NAME` at call time, so a test or an experiment can override a value
on this module and the whole agent follows."""

import os

# --- Negotiation pace -------------------------------------------------------------------------

# Share of the remaining gap to the supplier's counter that one of our sheets closes.
STEP_SHARE = 0.3
# Opening price as a share of the supplier's quote (at our planned terms), per category goal. On
# practice cases the best reachable deal was 12-22 % under the quote for leverage, routine and
# secure-supply goals, but only 1.5-3 % under it for stabilise goals.
OPENING_SHARE = {
    "leverage": 0.78,
    "routine": 0.82,
    "secure_supply": 0.88,
    "quality_first": 0.88,
    "stabilise": 0.92,
}
OPENING_SHARE_DEFAULT = 0.85
OPENING_SHARE_MIN = 0.75  # an opening under this share reads as an insult and burns patience
# Patience: the cumulative relative gap between our sheets and its counters a supplier tolerates.
PATIENCE_DEFAULT = 1.0  # effectively unlimited for an ordinary supplier
PATIENCE_RELATIONSHIP = 0.3
PATIENCE_FULLY_BOOKED = 0.16
PATIENCE_NEAR_COST = 0.14
PATIENCE_TOUCHY = 0.2  # under this, every remaining gap is kept small
TOUCHY_MAX_GAP = 0.015
# Opening-share floors for suppliers that cannot or will not move far on list price.
OPENING_SHARE_FROZEN = 0.95
OPENING_SHARE_FULLY_BOOKED = 0.93
OPENING_SHARE_NEAR_COST = 0.97
NEAR_COST_RATIO = 0.98  # its quote is within this ratio of our own-plant should-cost
# A first concession at least this large (share of its price) signals a deep floor: we hold.
DEEP_FIRST_DROP = 0.06
MIN_STEP = 0.002  # every new sheet rises by at least this share of our last price
MAX_SHARE_OF_COUNTER = 0.998  # never offer at or above its own counter
HOLD_FACTOR = 0.7  # the reader says "hold": we move this share of the usual step
CLAIMS_FIRST_ROUND = 3
CLAIMS_LATER_ROUNDS = 1
# A one-term alternative is only offered priced between these shares of what the supplier quotes for it.
TRADE_PRICE_MIN_SHARE = 0.7
TRADE_PRICE_MAX_SHARE = 0.995
NEED_TRADES_MAX = 2  # supplier-need alternatives shown next to the main sheet
PREP_TRADES_SHOWN = 2  # prepared alternatives shown next to the opening sheet
SWEET_SPOT_FLOOR_SHARE = 0.85  # relationship search starts at this share of the full cost
RELATIONSHIP_SEARCH_STEPS = 200
CHEAPER_PACKAGE_MIN_EUR = 1.0  # a quoted package must save at least this much ...
CHEAPER_PACKAGE_MIN_SHARE = 0.001  # ... or this share of our planned cost before we switch to it
MIRROR_MIN_SAVING_EUR = 1.0  # an alternative sheet is echoed back only if it saves this much
SAME_PRICE_TOLERANCE = 0.005  # two prices within this are the same offer
# Extra cost per day of faster delivery than quoted, as a share of the price.
RUSH_COST_FULLY_BOOKED = 0.005
RUSH_COST_DEFAULT = 0.001
CASH_NEED_RATE_BONUS = 0.05  # a supplier short of cash values money this much more per year

# --- Index clauses ----------------------------------------------------------------------------

# Highest value a supplier is assumed to put on an index clause, as a share of the price. One
# supplier valued its clause at 8.5 %; a linear model on copper's year would claim 24 %.
INDEX_CLAUSE_VALUE_CAP = 0.08
INDEX_CLAUSE_MIN_GAIN = 0.02  # its value minus our cost, as a share of the price

# --- Clocks and the session loop --------------------------------------------------------------

THREAD_CLOCK_S = 60  # each thread's clock, counted from the supplier's reply
CLOCK_RESERVE_S = 5  # kept free on every clock for building and delivering a message
KEEPALIVE_AFTER_S = 35  # a finished thread gets a short question this long after its reply
FIRST_ROUND_TRIP_S = 10  # assumed duration of one send until a real one has been measured
CLOSE_ATTEMPTS = 3  # a 409 on accept/walk: re-read the state and decide again
MOVE_FAILURES_MAX = 2  # after this many crashed moves a thread is set aside
RESUME_DELAY_S = 5  # a crashed final case is resumed after this pause ...
RESUME_MARGIN_S = 120  # ... while its wave cap still leaves this much time ...
RESUME_ATTEMPTS_MAX = 40  # ... and never more often than this
WAVE_POLL_S = 2.0

# --- Model calls ------------------------------------------------------------------------------

MODEL_TIMEOUT_S = 15.0  # wall clock a single model answer may take
MODEL_MIN_S = 2.0  # with less time than this on the clocks the model is not called at all
MODEL_WORKERS = 96  # background threads for model calls (10 cases with their prep teams)
READER_TIMEOUT_S = 6.0  # the reply reader's own cap
READER_MIN_CLOCK_S = 10.0  # the reader only runs when reader + writer still have this much time
PARAGRAPH_MAX_CHARS = 900

# --- Preparation team -------------------------------------------------------------------------

PREP_BUDGET_S = 40.0  # the whole team per case, before the session opens
PREP_STAGE_S = 13.0  # one stage: specialists; then lead + sparring supplier; then sparring buyer
RESEARCH_MODE = "alpha"  # alpha worktree default; baseline keeps main's four-specialist pipeline
ALPHA_RESEARCH_S = 24.0  # parallel experts and bounded discovery, leaving time for synthesis
ALPHA_SEARCH_ROUNDS = 3
ALPHA_TOOL_CALLS = 10
# Hypothesis learning (feature/hypothesis-learning): the research agents propose testable moves, code
# plays the most probable valid one and updates the probabilities from the supplier's replies.
# Off unless HACKIT_HYPOTHESES is set, so a run is unchanged until a comparison shows a gain.
HYPOTHESES = os.environ.get("HACKIT_HYPOTHESES", "").strip().lower() in ("1", "true", "yes", "on")
ALPHA_SQL_TIMEOUT_S = 0.5
PREP_TRADES_MAX = 2
LESSONS_RECALLED = 5

# --- Live telemetry (watch UI) ----------------------------------------------------------------

# Off in no-GUI mode (`--no-gui` or HACKIT_NO_GUI=1): no live files are written at all.
LIVE_TELEMETRY = os.environ.get("HACKIT_NO_GUI", "").strip().lower() not in ("1", "true", "yes")
GUI_PORT = 8790
