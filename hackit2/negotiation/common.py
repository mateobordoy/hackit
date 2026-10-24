"""Small helpers shared by the negotiation modules."""

import logging

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
