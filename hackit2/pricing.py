"""Pure arithmetic on term sheets: what a sheet costs us (C), what it is worth to the supplier, which
non-price terms to ask for, and the price limits the negotiation must respect.

No model is ever called here; every number that ends up on a term sheet is produced by this module.

A *valuation* is our picture of one supplier, passed to the kit as its `supplier` argument. Besides
the kit's own keys (`standard`, `interest_rate`, `ppm`, `full_cost`) it carries:
  quoted_lead      the lead time the supplier quoted (days)
  rush_cost_share  what each day faster than `quoted_lead` costs it, as a share of the price
  index_trend      series -> its change over the last twelve months (share)
"""

import itertools
import math

from arena_kit.cost import (
    RELATIONSHIP_GOALS,
    closing_terms,
    complete_offer,
    must_haves_ok,
    supplier_value,
    total_cost,
)

from . import settings, telemetry

__all__ = [
    "RELATIONSHIP_GOALS",
    "acceptable_cost",
    "best_index_clause",
    "build_valuation",
    "buyer_cost",
    "cheapest_terms",
    "complete_offer",
    "exact_package",
    "fallback_cost",
    "must_haves_ok",
    "price_ceiling",
    "price_for_worth",
    "priced",
    "relationship_sweet_spot",
    "supplier_worth",
]


def priced(terms, price):
    """A copy of `terms` at `price`, rounded to the cent."""
    return {**terms, "unit_price": round(price, 2)}


def buyer_cost(case, terms, standard=None, valuation=None):
    """C in EUR, or infinity when the kit cannot price the sheet. The relationship charge is only
    counted when the valuation holds a full-cost estimate."""
    if valuation is not None and not valuation.get("full_cost"):
        valuation = None
    try:
        return total_cost(case, terms, standard, valuation)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return math.inf


def fallback_cost(case):
    """C of the buyer's alternative, or infinity when there is none."""
    alternative = case["buyer"].get("alternative") or {}
    terms = alternative.get("terms") or {}
    if alternative.get("kind") in (None, "none") or not terms.get("unit_price"):
        return math.inf
    return buyer_cost(case, terms)


def acceptable_cost(case, terms, standard, valuation=None):
    """The acceptance gate: C of a priced sheet that meets every must-have and beats the alternative
    (relationship charge included), otherwise None. The returned C is exactly the C that was checked."""
    if not terms.get("unit_price") or not must_haves_ok(case, terms, standard):
        return None
    c = buyer_cost(case, terms, standard, valuation)
    return c if c < fallback_cost(case) else None


@telemetry.traced("pricing.price_ceiling")
def price_ceiling(case, terms, standard, valuation=None):
    """The highest price at `terms` that still beats the alternative by at least a cent; None when
    the case has no alternative, 0.0 when no positive price beats it.

    C is convex in the price (the relationship charge falls as the price rises), so we first locate
    C's minimum with a ternary search and only then bisect upward to the alternative's level."""
    limit = fallback_cost(case)
    if math.isinf(limit):
        return None

    def c_at(price):
        return buyer_cost(case, priced(terms, price), standard, valuation)

    upper = max(1.0, terms.get("unit_price") or 1.0) * 3
    a, b = 0.01, upper
    for _ in range(100):
        third_a, third_b = a + (b - a) / 3, b - (b - a) / 3
        if c_at(third_a) <= c_at(third_b):
            b = third_b
        else:
            a = third_a
    low = (a + b) / 2
    if not c_at(low) < limit:
        return 0.0
    high = upper
    for _ in range(60):
        mid = (low + high) / 2
        if c_at(mid) < limit:
            low = mid
        else:
            high = mid
    return max(0.0, round(math.floor(low * 100) / 100 - 0.01, 2))


def exact_package(case, terms, standard=None):
    """The sheet we would send to accept exactly the package `terms` describes, without snapping any
    value; None when that package is not a valid, must-have-compliant offer."""
    effective = closing_terms(case, terms, standard)
    if any(effective.get(k) != v for k, v in case.get("fixed_terms", {}).items()):
        return None
    offer = {field: effective.get(field) for field in case["negotiable"]}
    price = offer.get("unit_price")
    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return None
    allowed = case.get("term_options", {})
    if any(allowed.get(k) and v not in allowed[k] for k, v in offer.items()):
        return None
    if closing_terms(case, offer, standard) != effective:
        return None
    return offer if must_haves_ok(case, offer, standard) else None


def with_priced_incoterm(case, terms):
    """`terms` with an Incoterm the case has a freight rate for. An unknown one would raise in the
    kit, so it is replaced by the costliest known one, as the kit does for a missing value."""
    freight = case["buyer"]["freight_eur_per_unit"]
    if "incoterm" not in terms or terms.get("incoterm") in freight:
        return terms
    return {**terms, "incoterm": max(freight, key=freight.get)}


def build_valuation(case, standard, rate, full_cost=None, ppm=None, quoted_lead=None, rush_cost_share=0.0):
    """A fresh valuation of one supplier (see the module docstring)."""
    base = with_priced_incoterm(case, closing_terms(case, {}, with_priced_incoterm(case, standard or {})))
    valuation = {
        "standard": base,
        "interest_rate": rate,
        "quoted_lead": quoted_lead,
        "rush_cost_share": rush_cost_share,
    }
    if ppm is not None:
        valuation["ppm"] = ppm
    if full_cost:
        valuation["full_cost"] = full_cost
    valuation["index_trend"] = {}
    return valuation


# --- index clauses ----------------------------------------------------------------------------


def clause_worth_share(clause, valuation):
    """What an index clause is worth to the supplier as a share of the price: it expects its driver
    to keep moving as it did over the past year and the clause passes on the part beyond the
    deadband, capped at `settings.INDEX_CLAUSE_VALUE_CAP`. None when the driver's trend is unknown."""
    trend = valuation.get("index_trend", {}).get(clause.get("series"))
    if trend is None:
        return None
    beyond = max(0.0, trend - (clause.get("deadband_pct") or 0) / 100)
    return min(settings.INDEX_CLAUSE_VALUE_CAP, (clause.get("material_share") or 0) * beyond)


def clause_worth(terms, valuation):
    """What the sheet's index clause is worth to the supplier per unit (0 without a clause)."""
    clause = terms.get("index_clause")
    if not clause:
        return 0.0
    share = clause_worth_share(clause, valuation)
    return 0.0 if share is None else share * (terms.get("unit_price") or 0)


def clause_cost_share(case, clause):
    """What a clause costs us as a share of spend: the case's index path beyond the deadband plus
    the index risk charge."""
    buyer = case["buyer"]
    share = clause.get("material_share") or 0
    deadband = (clause.get("deadband_pct") or 0) / 100
    path = abs(buyer.get("index_paths", {}).get(clause.get("series"), 0.0))
    return share * max(0.0, path - deadband) + share * buyer.get("index_risk_pct", 0) / 100


@telemetry.traced("pricing.best_index_clause")
def best_index_clause(case, valuation, min_gain=None):
    """The allowed index clause whose value to the supplier exceeds its cost to us by the most (at
    least `min_gain` of the price); among equal gains the widest deadband. None when none pays."""
    min_gain = settings.INDEX_CLAUSE_MIN_GAIN if min_gain is None else min_gain
    best_key, best_clause = None, None
    for clause in case.get("term_options", {}).get("index_clause") or []:
        if not clause:
            continue
        worth = clause_worth_share(clause, valuation)
        if worth is None:
            continue
        gain = worth - clause_cost_share(case, clause)
        key = (round(gain, 4), clause.get("deadband_pct") or 0)
        if gain >= min_gain and (best_key is None or key > best_key):
            best_key, best_clause = key, clause
    return best_clause


# --- the supplier's side ----------------------------------------------------------------------


def supplier_worth(case, terms, valuation):
    """What a sheet is worth to the supplier per unit, in cash at its standard terms, plus its index
    clause, minus the overtime a delivery faster than it quoted would cost it."""
    worth = supplier_value(case, with_priced_incoterm(case, terms), valuation) + clause_worth(terms, valuation)
    lead, quoted = terms.get("lead_time_days"), valuation.get("quoted_lead")
    rush = valuation.get("rush_cost_share")
    if rush and lead is not None and quoted and lead < quoted:
        worth -= rush * terms["unit_price"] * (quoted - lead)
    return worth


def price_for_worth(case, terms, valuation, target):
    """The unit price at which `terms` are worth `target` to the supplier (worth rises with price)."""
    low, high = 0.0, max(1.0, target) * 3
    for _ in range(60):
        mid = (low + high) / 2
        if supplier_worth(case, {**terms, "unit_price": mid}, valuation) < target:
            low = mid
        else:
            high = mid
    return high


def term_grid(case):
    """Every combination of allowed non-price values, as [{field: value}]."""
    allowed = case.get("term_options") or {}
    fields = [f for f in case["negotiable"] if f != "unit_price" and allowed.get(f)]
    return [dict(zip(fields, combo, strict=True)) for combo in itertools.product(*(allowed[f] for f in fields))]


# Direction the supplier prefers on fields that cost us nothing extra: later delivery (no overtime),
# a longer contract and more volume (security), a looser defect commitment and lower chargebacks.
SUPPLIER_LEANING = {
    "lead_time_days": 1,
    "contract_months": 1,
    "volume_per_year": 1,
    "ppm_commitment": 1,
    "chargeback_eur_per_complaint": -1,
    "tooling_eur": 1,
}


def supplier_preference(terms):
    """Sort key: higher means the supplier likes the sheet better."""
    return tuple(SUPPLIER_LEANING[f] * (terms.get(f) or 0) for f in SUPPLIER_LEANING if f in terms)


@telemetry.traced("pricing.cheapest_terms", result_view=telemetry.plan_view)
def cheapest_terms(case, standard, valuation, reference_terms, reference_price):
    """The non-price terms that cost us least once the supplier reprices them at its own valuation.

    Every allowed combination is priced so it is worth to the supplier what `reference_price` at
    `reference_terms` is worth; the cheapest C wins, ties go to the supplier's favourite. Returns
    (terms at that price, C) or None when no combination meets the must-haves."""
    worth = supplier_worth(case, {**reference_terms, "unit_price": reference_price}, valuation)
    candidates = []
    for combo in term_grid(case) or [{}]:
        terms = complete_offer(case, {**reference_terms, **combo, "unit_price": reference_price})
        terms["unit_price"] = round(price_for_worth(case, terms, valuation, worth), 2)
        if must_haves_ok(case, terms, standard):
            candidates.append((buyer_cost(case, terms, standard, valuation), terms))
    if not candidates:
        return None
    lowest = min(c for c, _ in candidates)
    tied = [(c, t) for c, t in candidates if c <= lowest + max(1.0, abs(lowest) * 1e-5)]
    c, terms = max(tied, key=lambda row: supplier_preference(row[1]))
    return terms, c


@telemetry.traced("pricing.relationship_sweet_spot")
def relationship_sweet_spot(case, terms, standard, valuation, low, high):
    """Relationship goals only: the price in [low, high] with the lowest C, relationship charge
    included (C falls with the price until the margin charge grows faster). None for other goals or
    without a full-cost estimate."""
    if case["buyer"].get("category_goal") not in RELATIONSHIP_GOALS or not valuation.get("full_cost"):
        return None
    steps = settings.RELATIONSHIP_SEARCH_STEPS
    best_price, best_c = low, math.inf
    for i in range(steps + 1):
        price = low + (high - low) * i / steps
        c = buyer_cost(case, priced(terms, price), standard, valuation)
        if c < best_c - 1e-6:
            best_price, best_c = price, c
    return round(best_price, 2)
