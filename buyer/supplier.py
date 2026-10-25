"""What we know about one supplier before and during the negotiation: owner type, persona traits
found in its notes, letters, quote remarks, news and its own e-mails, its cost of money (learned
online from its priced alternatives when it sends any), its full cost, and the prior over where
it will stop. Unknown suppliers get the neutral profile and learn from their e-mails."""

import re

from . import config

# Persona traits appear as one or two fixed phrases in the generated texts; widened to the word
# forms seen across the package. [pc] A hidden supplier's own e-mails are scanned the same way.
SIGNALS = {
    "cash_need": r"pay earlier|bank is watching|cash and the banks|within 10 days|under pressure"
    r"|restructur\w*|short-time work|lower\w* (its |their )?rating|cash is tight|we like fast payers"
    r"|negotiat\w+ with (its|their) banks|lowers our margin|loss-making|liquidity|waiting for cash"
    r"|insolven\w*|prepayment|credit insurer",
    "spare_capacity": r"second shift has been on hold|take additional parts|looks for new customers"
    r"|free (machine )?capacity|keep (the|our) machines (busy|running)|spare capacity",
    "capacity_full": r"order intake is very strong|binding 12-month commitment"
    r"|cannot confirm your full forecast|fully booked|allocat\w+ (volumes|in line)|factory is super busy"
    r"|high capacity utili[sz]ation|prioriti[sz]e customers with firm|capacity is (tight|full)",
    "open_book": r"walk (us|you) through (their|our) calculation",
    "stale_costing": r"not followed (his|its|their) material|own spreadsheet",
    "partnership_2022": r"kept supplying at the old price",
    "approval_2pct": r"above 2 ?% need the approval|pricing committee",
    "deprioritising_us": r"reviewing small accounts|not strategic|reorganising our range"
    r"|supply from stock only|sharpens its focus|focus on customers with volume",
    "price_frozen": r"no room on price|not discounted|price list (\d{4} )?applies to all|discounts not possible"
    r"|frozen all price reductions|froze price reductions|(ruled out|no longer grant|cannot grant|no) individual"
    r" price reductions|prices? (are|will be) set centrally|head-office directive",
    "hedged": r"bought (their|our) [\w ,-]{1,60} forward|covered at fixed prices|material covered until"
    r"|secures power prices|hedged",
    "complains_late_pay": r"invoices are overdue|paid late|late payments",
}
TOUCHY = {"capacity_full", "price_frozen"}


def signals_in(text):
    return sorted(k for k, pat in SIGNALS.items() if re.search(pat, text or "", re.I))


def supplier_text_items(data, sid):
    """[(source ID, text)] for every text about this supplier: letters, call and meeting notes,
    news (package, Sunday release and a hidden case's own facts alike: all reach the ID index),
    plus its quote remarks (source: the quote ID)."""
    items = []
    for ref in data.ids("doc:") + data.ids("news:"):
        row = data.fact(ref) or {}
        if sid not in (row.get("supplier"), row.get("supplier_id")):
            continue
        text = data.document(ref)
        if not text:
            text = " ".join(str(v) for k, v in row.items() if k in ("title", "headline", "body", "subject", "text"))
        items.append((ref, text))
    for r in data.query("SELECT id, remarks FROM quotes WHERE supplier_id = ?", (sid,)):
        if r.get("remarks"):
            items.append((r.get("id") or "quote", r["remarks"]))
    return items


def supplier_texts(data, sid):
    return [text for _, text in supplier_text_items(data, sid)]


def profile(data, sid, first_mail=""):
    """{owner_type, country, signals, rate, warm, known}"""
    out = {"owner_type": None, "country": None, "signals": [], "rate": config.DEFAULT_RATE, "warm": True}
    known = data.known_supplier(sid)
    texts = supplier_texts(data, sid) if known else []
    sup = data.query("SELECT country, skonto_days, skonto_pct FROM suppliers WHERE id = ?", (sid,))
    intel = data.query("SELECT owner_type FROM supplier_intel WHERE supplier_id = ?", (sid,))
    owner = intel[0].get("owner_type") if intel else None
    sig = signals_in("\n".join([*texts, first_mail or ""]))
    rate = config.OWNER_RATE.get(owner, config.DEFAULT_RATE)
    if "cash_need" in sig:
        rate += config.CASH_NEED_BONUS
    if sup and sup[0].get("skonto_pct") and sup[0].get("skonto_days"):
        # a cash discount of s % for paying d days early is worth s / d * 360 % a year to it;
        # halved: the discount is also a sales habit, not only its cost of money [pc]
        days_early = max(1, 30 - int(sup[0]["skonto_days"]))
        rate = max(rate, min(0.2, sup[0]["skonto_pct"] / 100 * 360 / max(days_early, 10) / 2))
    out.update(
        owner_type=owner,
        country=sup[0].get("country") if sup else None,
        signals=sig,
        rate=round(rate, 4),
        warm=owner in (None, "family", "founder", "private"),
        known=known,
    )
    return out


def read_signals(prof, body):
    """New traits in its e-mail; a cash need raises what money now is worth to it. Returns them."""
    new = [k for k in signals_in(body) if k not in prof["signals"]]
    if new:
        prof["signals"] = sorted(prof["signals"] + new)
        if "cash_need" in new:
            prof["rate"] = round(prof["rate"] + config.CASH_NEED_BONUS, 4)
    return new


def full_cost(data, part, sid):
    """(should-cost of the part, whether it models this supplier's own plant)."""
    rows = data.query("SELECT id, supplier_id, unit_cost FROM should_cost WHERE part_id = ?", (part,))
    own = [r for r in rows if r.get("supplier_id") == sid and r.get("unit_cost")]
    if own:
        return float(own[0]["unit_cost"]), True
    generic = [r for r in rows if not r.get("supplier_id") and r.get("unit_cost")]
    if generic:
        return float(generic[0]["unit_cost"]), False
    for ref in data.ids("cost:"):  # a hidden case's own facts
        f = data.fact(ref)
        if f and f.get("part") == part and f.get("unit_cost"):
            return float(f["unit_cost"]), f.get("supplier") == sid
    return None, False


def real_ppm(data, sid):
    """The defect rate it really delivers (latest scorecard): what a chargeback costs it."""
    ids = data.ids(f"sc:{sid.split(':')[-1]}@")
    f = data.fact(ids[-1]) if ids else None
    return f.get("ppm") if f else None


def floor_prior(case, prof, near_cost=False):
    """(mean, sd) of where it stops, as a share of its opening quote at our terms."""
    mean, sd = config.FLOOR_PRIOR.get(case.get("type"), config.FLOOR_PRIOR_DEFAULT)
    goal = case["buyer"].get("category_goal")
    if goal == "stabilise" and case.get("type") not in ("price_increase", "renewal"):
        mean = max(mean, 0.95)
    sig = set(prof.get("signals") or [])
    if "price_frozen" in sig:
        mean, sd = max(mean, 0.985), min(sd, 0.012)
    if "capacity_full" in sig:
        mean = max(mean, 0.965)
    if near_cost:
        mean, sd = max(mean, 0.98), min(sd, 0.015)
    if prof.get("owner_type") == "founder":  # job shops conceded up to 22 % [pc]
        mean, sd = mean - 0.03, sd + 0.02
    if sig & {"spare_capacity", "cash_need"}:
        mean -= 0.01
    return min(mean, 0.995), max(sd, 0.008)


def patience_prior(case, prof, near_cost=False):
    goal = case["buyer"].get("category_goal")
    touchy = near_cost or goal in ("stabilise", "secure_supply") or set(prof.get("signals") or []) & TOUCHY
    return config.PATIENCE_PRIOR_TOUCHY if touchy else config.PATIENCE_PRIOR


def learn_rate(sheets, std_days):
    """Its cost of money from two of its own sheets that differ only in payment days and price
    (`alternatives` it offers): p1 (1 - r (d1 - s)/360) = p2 (1 - r (d2 - s)/360). None if no pair."""
    found = []
    for i, a in enumerate(sheets):
        for b in sheets[i + 1 :]:
            diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
            if diff != {"unit_price", "payment_days"}:
                continue
            p1, d1, p2, d2 = a["unit_price"], a["payment_days"], b["unit_price"], b["payment_days"]
            s = std_days or 30
            denom = p1 * (d1 - s) - p2 * (d2 - s)
            if abs(denom) < 1e-9:
                continue
            r = 360 * (p1 - p2) / denom
            lo, hi = config.RATE_BOUNDS
            if lo <= r <= hi:
                found.append(r)
    return sum(found) / len(found) if found else None
