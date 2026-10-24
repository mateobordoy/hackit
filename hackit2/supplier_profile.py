"""What our data says about a supplier before the first e-mail: who owns it, which traits show in its
letters, our notes, its quote remarks and the news (package and Sunday release alike), and from
those, what money is worth to it. A supplier the package does not know gets the neutral profile;
its own e-mails are scanned for the same traits as they arrive (`traits_in`).

A profile is a dict: ownership, country, category, traits (sorted list), money_rate, warm."""

import re

from . import settings

# One or two stock phrases per trait in the generated texts, widened to the word forms that occur
# across the whole package.
TRAIT_PATTERNS = {
    "cash_need": r"pay earlier|bank is watching|cash and the banks|within 10 days|under pressure"
    r"|restructur\w*|short-time work|lower\w* (its |their )?rating|cash is tight|we like fast payers"
    r"|negotiat\w+ with (its|their) banks|lowers our margin|loss-making|liquidity|waiting for cash"
    r"|insolven\w*|prepayment|credit insurer",
    "spare_capacity": r"second shift has been on hold|take additional parts|looks for new customers"
    r"|free (machine )?capacity|keep (the|our) machines (busy|running)",
    "capacity_full": r"order intake is very strong|binding 12-month commitment"
    r"|cannot confirm your full forecast|fully booked|allocat\w+ (volumes|in line)|factory is super busy"
    r"|high capacity utili[sz]ation|prioriti[sz]e customers with firm",
    "open_book": r"walk (us|you) through (their|our) calculation",
    "stale_costing": r"not followed (his|its|their) material|own spreadsheet",
    "partnership_2022": r"kept supplying at the old price",
    "reference_customer": r"important reference customer",
    "approval_2pct": r"above 2 ?% need the approval|pricing committee",
    "deprioritising_us": r"reviewing small accounts|not strategic|reorganising our range"
    r"|supply from stock only|sharpens its focus|focus on customers with volume",
    "price_frozen": r"no room on price|not discounted|price list (\d{4} )?applies to all|discounts not possible"
    r"|frozen all price reductions|(ruled out|no longer grant|cannot grant|no) individual price reductions"
    r"|prices? (are|will be) set centrally",
    "hedged": r"bought (their|our) [\w ,-]{1,60} forward|covered at fixed prices|material covered until"
    r"|secures power prices|hedged",
    "complains_late_pay": r"invoices are overdue",
}
# Yearly cost of money by ownership, as observed in practice (groups 4-7 %, family firms 7-8.5 %,
# founder-run job shops 12-17 %); a cash need adds to it.
MONEY_RATE = {"group": 0.05, "private": 0.07, "family": 0.075, "founder": 0.12}
MONEY_RATE_DEFAULT = 0.07
SKONTO_RATE_CAP = 0.2
WARM_OWNERSHIPS = (None, "family", "founder", "private")


def traits_in(text):
    """The trait names whose pattern occurs in `text`, sorted."""
    return sorted(name for name, pattern in TRAIT_PATTERNS.items() if re.search(pattern, text or "", re.I))


def texts_about(pkg, supplier_id):
    """Every text about this supplier: letters, call and meeting notes, news (package and Sunday
    release both reach `ids` and `document`), plus the remarks on its quotes."""
    texts = []
    for ref in pkg.ids("doc:") + pkg.ids("news:"):
        row = pkg.lookup(ref) or {}
        if row.get("supplier") != supplier_id and row.get("supplier_id") != supplier_id:
            continue
        try:
            texts.append(pkg.document(ref))
        except Exception:  # noqa: BLE001 - no readable file: the row's headline still counts
            texts.append(" ".join(str(v) for k, v in row.items() if k in ("title", "headline", "body", "subject")))
    try:
        remarks = pkg.query("SELECT remarks FROM quotes WHERE supplier_id = ?", (supplier_id,))
        texts += [row.get("remarks") or "" for row in remarks]
    except Exception:  # noqa: BLE001 - no SQLite
        pass
    return texts


def neutral_profile():
    return {
        "ownership": None,
        "country": None,
        "category": None,
        "traits": [],
        "money_rate": MONEY_RATE_DEFAULT,
        "warm": True,
    }


def build_profile(pkg, supplier_id):
    """The supplier's profile; neutral when the package does not know it."""
    profile = neutral_profile()
    if pkg is None or not pkg.lookup(supplier_id):
        return profile
    try:
        vendor = pkg.query(
            "SELECT country, category, skonto_days, skonto_pct FROM suppliers WHERE id = ?", (supplier_id,)
        )
        intel = pkg.query("SELECT owner_type, staff FROM supplier_intel WHERE supplier_id = ?", (supplier_id,))
    except Exception:  # noqa: BLE001 - no SQLite: neutral
        return profile
    traits = traits_in("\n".join(texts_about(pkg, supplier_id)))
    ownership = intel[0]["owner_type"] if intel else None
    rate = MONEY_RATE.get(ownership, MONEY_RATE_DEFAULT)
    if "cash_need" in traits:
        rate += settings.CASH_NEED_RATE_BONUS
    if vendor and vendor[0].get("skonto_pct") and vendor[0].get("skonto_days"):
        # a cash discount of s % for paying d days early is worth s / d * 360 % a year to it
        days_early = max(1, 30 - int(vendor[0]["skonto_days"]))
        rate = max(rate, min(SKONTO_RATE_CAP, vendor[0]["skonto_pct"] / 100 * 360 / max(days_early, 10) / 2))
    profile.update(
        ownership=ownership,
        country=vendor[0]["country"] if vendor else None,
        category=vendor[0]["category"] if vendor else None,
        traits=traits,
        money_rate=round(rate, 3),
        warm=ownership in WARM_OWNERSHIPS,
    )
    return profile
