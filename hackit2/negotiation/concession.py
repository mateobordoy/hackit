"""How far our price moves each round. Pure arithmetic, shared by the live negotiation."""

from .. import settings, telemetry


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
    if patience < settings.PATIENCE_TOUCHY:
        new_gap = min(new_gap, settings.TOUCHY_MAX_GAP)
    price = counter * (1 - new_gap)
    price = max(price, last * (1 + settings.MIN_STEP))
    price = min(price, counter * settings.MAX_SHARE_OF_COUNTER)
    return max(price, last)
