"""The state we keep per supplier thread, plus readers for the supplier's quote and requests."""

import re

from arena_kit.docs import quote_terms, standard_terms

from .. import settings
from .common import ACTIVE

# What a supplier e-mail asks of us. The bots phrase their asks in a few stock sentences, so plain
# patterns read them with no model and no latency.
REQUEST_PATTERNS = {
    # a binding volume figure; stated in words with the figure, it was taken into account
    "volume": r"volume (can )?you (can )?commit|binding (higher )?volume|higher (order )?volume|volume figures"
    r"|confirm .{0,40}volume|anticipated (order )?volume",
    # proof of the forecast, or of figures we mentioned without an ID
    "proof": r"proof of|send us the data|figures is that based|provide the (volume|forecast|binding)"
    r"|forecast data|volume data",
    # it wants to be paid sooner
    "payment": r"faster payment|pay(ment)? (terms )?(earlier|faster|sooner|within)|need .{0,12}days payment"
    r"|shorter payment|quicker payment|cash flow",
    # it asks for our current price level or our target
    "target": r"what price|which price|your target|price you have in mind",
}


def detect_requests(body):
    """The kinds of request in a supplier e-mail, in the fixed order of `REQUEST_PATTERNS`."""
    text = body or ""
    return [kind for kind, pattern in REQUEST_PATTERNS.items() if re.search(pattern, text, re.I)]


def _line_parses(line):
    try:
        quote_terms(f"Our standard terms\n{line}")
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def quoted_standard_terms(events):
    """The supplier's standard terms from its latest quote PDF. A line the kit cannot parse is
    dropped on its own, so one odd value never loses the whole quote (or the supplier at the close)."""
    try:
        return standard_terms(events)
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    for message in reversed(events):
        for attachment in message.get("attachments") or []:
            if attachment.get("name", "").startswith("quote") and attachment.get("text"):
                lines = [line for line in attachment["text"].splitlines() if _line_parses(line)]
                return quote_terms("\n".join(lines)).get("standard", {})
    return {}


class SupplierThread:
    """One supplier thread: what we know about the supplier and what has happened so far."""

    def __init__(self, supplier_id):
        self.supplier_id = supplier_id
        self.standard = {}  # its standard terms (quote PDF)
        self.profile = {}  # `supplier_profile.build_profile`
        self.valuation = {}  # `pricing.build_valuation`
        self.plan = {}  # our planned terms; `unit_price` is a placeholder
        self.quote_at_plan = None  # its opening quote converted to our planned terms
        self.late_rounds = 0  # our messages the arena counted although they arrived too late
        self.opening_price = None
        self.sweet_spot = None  # relationship goals: the price with the lowest C
        self.patience = settings.PATIENCE_DEFAULT
        self.evidence = []  # arguments for this supplier, best first
        self.our_prices = []  # our price at `plan`, one per message carrying a sheet
        self.their_prices = []  # its price converted to `plan`, one per reply
        self.gap_history = []  # relative gap of each of our sheets to its counter at the time
        self.phase = ACTIVE
        self.outclassed = False  # a frozen sheet elsewhere beats any deal here: no rounds, no keepalives
        self.phase_before = ACTIVE  # the phase before the message in flight
        self.in_flight = None  # what to undo if the message in flight times out
        self.last_primary_sent = None  # our last delivered main sheet, for the no-progress guard
        self.used_topics = set()  # semantic argument families already used with this supplier
        self.shown_alternatives = set()  # one-term alternatives already offered in this thread
        self.counter_changed_terms = False  # its last counter changed our non-price terms
        self.last_seen_reply = None
        self.requests = []  # what its last e-mail asked of us
        self.files_sent = set()
        self.prep_notes = ""  # the lead researcher's notes for the e-mail writer
        self.prep_trades = []  # terms the lead expects it to value: offered next to our opening sheet
        self.need_trades = []  # supplier needs priced for the current move
        self.playbook = {}  # the sparring round: expected arguments, our answers, points to raise
        self.history = None  # `transcript.build`, from delivered messages only
        self.reader_notes = ""  # the reply reader's notes on its last e-mail
        self.mood = None
        self.tactic = None  # steady | hold | argue
        self.tactic_log = []  # every tactic played, for the debrief
        self.matched_argument = 0  # 1-based expected argument its last e-mail made, 0 for none
