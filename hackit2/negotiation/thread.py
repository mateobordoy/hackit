"""The state we keep per supplier thread, plus readers for the supplier's quote and requests."""

import logging
import re

from arena_kit.docs import quote_terms, standard_terms

from .. import settings, telemetry

# What a supplier e-mail asks of us. The bots phrase their asks in a few stock sentences, so plain
# patterns read them with no model and no latency.
log = logging.getLogger("hackit2")

# Thread phases
ACTIVE = "active"  # still bargaining
ECHO = "echo"  # we sent one of its own sheets back for it to agree to; the next reply ends the thread
SETTLED = "settled"  # nothing more to bargain; it waits for the close (and keepalives)
GONE = "gone"  # the supplier left, or never quoted
FINISHED = (SETTLED, GONE)

# Supplier statuses that freeze its standing sheet
FROZEN = {"agreed", "final_offer"}


def guarded(fn, default=None):
    """`fn()`, or `default` if it raises. A hidden case may lack a field the data code expects, and a
    missing argument or profile must never cost the move; the failure is logged."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - logged, the move goes on with the default
        log.exception("skipped: %s", getattr(fn, "__name__", fn))
        return default


@telemetry.traced("negotiation.concession.next_step_price")
def next_step_price(last, counter, first_drop, first_round, share, patience, patience_used, terms_changed=False):
    """Our next price after `last`, facing the supplier's counter `counter`.

    We close `share` of the gap, but hold our price after a deep first concession (a pure price drop
    that large means the supplier moves fast on its own and will agree at our sheet next, unless the
    drop came with terms it added), and keep the next gap within what is left of its patience."""
    gap = (counter - last) / counter
    closing = share
    if first_round and first_drop >= settings.DEEP_FIRST_DROP and not terms_changed:
        closing = 0.0
    remaining = max(0.0, patience - patience_used)
    new_gap = min(gap * (1 - closing), remaining / 2)
    if patience < settings.PATIENCE_TOUCHY:  # a touchy supplier: small, steady gaps
        new_gap = min(new_gap, settings.TOUCHY_MAX_GAP)
    price = counter * (1 - new_gap)
    price = max(price, last * (1 + settings.MIN_STEP))
    price = min(price, counter * settings.MAX_SHARE_OF_COUNTER)
    return max(price, last)


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
        self.history = None  # `mailer.build_transcript`, from delivered messages only
        self.reader_notes = ""  # the reply reader's notes on its last e-mail
        self.mood = None
        self.tactic = None  # steady | hold | argue
        self.tactic_log = []  # every tactic played, for the debrief
        self.matched_argument = 0  # 1-based expected argument its last e-mail made, 0 for none
