"""What we know and what we did in one supplier thread."""

from dataclasses import dataclass, field

from .belief import Belief, State


@dataclass
class Thread:
    sid: str
    std: dict = field(default_factory=dict)  # its standard terms (quote PDF)
    prof: dict = field(default_factory=dict)
    est: dict = field(default_factory=dict)  # our estimate of it for supplier_value
    terms: dict = field(default_factory=dict)  # our planned terms (price = our last price)
    q0: float | None = None  # its opening price at our planned terms (the belief's 1.0)
    anchor: float | None = None
    target: float | None = None  # relationship goals: the price with the lowest C
    near_cost: bool = False
    full_cost_known: bool = False  # from a should-cost, not assumed from the quote
    claims: list = field(default_factory=list)
    belief: Belief | None = None
    ours: list = field(default_factory=list)  # our prices at `terms`, one per sheet sent
    theirs: list = field(default_factory=list)  # its price at `terms`, one per reply
    gap_sum: float = 0.0
    holds: int = 0
    first_drop: float = 0.0
    term_change: bool = False
    state: str = "open"  # open | mirror | done | ended
    dominated: bool = False
    late: int = 0  # our messages the arena counted although they came too late
    last_key: str | None = None  # the supplier message last read
    sent: tuple | None = None  # (State, normalised price) of our last sheet, for the belief
    pending: dict | None = None  # the message in flight: what to undo on a timeout
    last_sent_terms: dict | None = None
    last_sent_sheets: list = field(default_factory=list)  # every package of our last message
    used_trade_keys: set = field(default_factory=set)
    probe: dict | None = None  # round-1 payment alternative: {"days", "rate"} to learn its rate from its pick
    asks: list = field(default_factory=list)
    reader: dict | None = None  # the reader agent's latest reading of this supplier
    replanned: bool = False
    refusals: dict = field(default_factory=dict)  # field -> replies in a row that set its own value
    last_status: str | None = None

    def norm(self, price):
        return price / self.q0

    def state_now(self):
        """The observed state before our next sheet."""
        return State(
            k=len(self.ours) + 1,
            c_prev=self.norm(self.theirs[-1]),
            x_prev=self.norm(self.ours[-1]) if self.ours else None,
            gap_sum=self.gap_sum,
            holds=self.holds,
        )
