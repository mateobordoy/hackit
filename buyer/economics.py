"""Numbers only: the total cost C of a sheet, the accept gate, what a sheet is worth to the
supplier, the non-price terms we plan, the alternative's price ceiling and, in relationship goals,
the price where C is lowest. Nothing here calls a model."""

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

from . import config

MAX_COMBOS = 20000  # [eng] beyond this, plan terms field by field


def cost(case, terms, std=None, est=None):
    """C in EUR; infinite for a sheet the cost function cannot price. The relationship charge
    needs a full-cost estimate; without one it counts as 0."""
    if est is not None and not est.get("full_cost"):
        est = None
    try:
        return total_cost(case, known_incoterm(case, terms), std and known_incoterm(case, std), est)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError):
        return math.inf


def c_alt(case):
    alt = case["buyer"].get("alternative") or {}
    terms = alt.get("terms") or {}
    if alt.get("kind") in (None, "none") or not terms.get("unit_price"):
        return math.inf
    return cost(case, terms)


def acceptable(case, terms, std=None, est=None):
    """The accept gate: C of a priced sheet that keeps every must-have and beats the alternative
    (relationship charge included), else None."""
    if not terms or not terms.get("unit_price"):
        return None
    try:
        if not must_haves_ok(case, terms, std):
            return None
    except (KeyError, TypeError):
        return None
    c = cost(case, terms, std, est)
    return c if c < c_alt(case) else None


def known_incoterm(case, terms):
    """`terms` with an Incoterm the case prices; an unknown one falls back to the costliest, as the
    kit does for a gap (an unknown Incoterm would be a KeyError in the kit)."""
    if not terms:
        return terms
    freight = case["buyer"].get("freight_eur_per_unit") or {}
    if "incoterm" not in terms or terms.get("incoterm") in freight or not freight:
        return terms
    return {**terms, "incoterm": max(freight, key=freight.get)}


def at_price(terms, price):
    return {**terms, "unit_price": round(price, 2)}


# -- the supplier's view ------------------------------------------------------------------------
def estimate(case, std, rate, full_cost=None, ppm=None, lead=None, overtime=0.0, index_moves=None, hedged=False):
    """Our view of the supplier for `supplier_value` and the relationship charge."""
    standard = known_incoterm(case, closing_terms(case, {}, known_incoterm(case, std or {})))
    est = {
        "standard": standard,
        "interest_rate": rate,
        "lead": lead,
        "overtime": overtime,
        "index_moves": dict(index_moves or {}),
        "hedged": hedged,
    }
    if ppm is not None:
        est["ppm"] = ppm
    if full_cost:
        est["full_cost"] = full_cost
    return est


def clause_share(clause, est):
    """What an index clause is worth to the supplier as a share of the price: it expects its
    driver to move on as over the last year; capped (one supplier valued it at 8.5 %)."""
    if not clause or est.get("hedged"):
        return 0.0
    move = (est.get("index_moves") or {}).get(clause.get("series"))
    if move is None:
        return 0.0
    exposed = max(0.0, move - (clause.get("deadband_pct") or 0) / 100)
    return min(config.CLAUSE_VALUE_MAX, (clause.get("material_share") or 0) * exposed)


def value(case, terms, est):
    """What a sheet is worth to the supplier per unit, in cash at its standard terms, plus its
    clause value, less the overtime a faster delivery than it quoted costs it."""
    t = known_incoterm(case, terms)
    v = supplier_value(case, t, est)
    v += clause_share(t.get("index_clause"), est) * (t.get("unit_price") or 0)
    lead, quoted = t.get("lead_time_days"), est.get("lead")
    if est.get("overtime") and lead is not None and quoted and lead < quoted:
        v -= est["overtime"] * (t.get("unit_price") or 0) * (quoted - lead)
    return v


def price_for_value(case, terms, est, target):
    """The unit price at which `terms` are worth `target` to the supplier. Value is affine in the
    price (every item is either constant or proportional to it), so two evaluations solve it."""
    v0 = value(case, {**terms, "unit_price": 0.0}, est)
    v1 = value(case, {**terms, "unit_price": 1.0}, est)
    slope = v1 - v0
    if slope <= 1e-9:
        return terms.get("unit_price") or 0.0
    return max(0.0, (target - v0) / slope)


def convert(case, terms, est, to_terms):
    """A sheet as a price at `to_terms`, worth the same to the supplier."""
    same = all(terms.get(k) == v for k, v in to_terms.items() if k != "unit_price" and k in terms)
    if same and terms.get("unit_price") is not None:
        return float(terms["unit_price"])
    return price_for_value(case, to_terms, est, value(case, terms, est))


# -- planning our terms ------------------------------------------------------------------------
# Where a value costs us nothing more, the side the supplier prefers. [pc]
FRIENDLY = {
    "lead_time_days": 1,
    "contract_months": 1,
    "volume_per_year": 1,
    "ppm_commitment": 1,
    "chargeback_eur_per_complaint": -1,
    "tooling_eur": 1,
}


def friendliness(terms):
    return tuple(FRIENDLY[f] * (terms.get(f) or 0) for f in FRIENDLY if f in terms)


def option_fields(case):
    opts = case.get("term_options") or {}
    return [f for f in case["negotiable"] if f != "unit_price" and opts.get(f)]


def options(case):
    opts = case.get("term_options") or {}
    fields = option_fields(case)
    n = math.prod(len(opts[f]) for f in fields) if fields else 1
    if n > MAX_COMBOS:
        return None
    return [dict(zip(fields, combo, strict=True)) for combo in itertools.product(*(opts[f] for f in fields))]


def _priced(case, std, est, ref_terms, combo, target):
    terms = complete_offer(case, {**ref_terms, **combo, "unit_price": 1.0})
    terms["unit_price"] = round(price_for_value(case, terms, est, target), 2)
    if terms["unit_price"] <= 0:
        return None
    try:
        if not must_haves_ok(case, terms, std):
            return None
    except (KeyError, TypeError):
        return None
    return terms


def plan_terms(case, std, est, ref_terms, ref_price):
    """The non-price terms that cost us least once the supplier prices them at its own valuation:
    for every allowed combination, the price worth the same to it as `ref_price` at `ref_terms`,
    then C there; among equal C the supplier's favourite. Returns (terms, C) or None."""
    target = value(case, {**ref_terms, "unit_price": ref_price}, est)
    combos = options(case)
    rows = []
    if combos is None:  # too many combinations: coordinate descent from the reference terms
        current = {f: ref_terms.get(f) for f in option_fields(case)}
        for _ in range(3):
            for f in option_fields(case):
                best = None
                for v in case["term_options"][f]:
                    terms = _priced(case, std, est, ref_terms, {**current, f: v}, target)
                    if terms is not None:
                        c = cost(case, terms, std, est)
                        if best is None or c < best[0]:
                            best = (c, v)
                if best:
                    current[f] = best[1]
        terms = _priced(case, std, est, ref_terms, current, target)
        if terms is not None:
            rows.append((cost(case, terms, std, est), terms))
    else:
        for combo in combos or [{}]:
            terms = _priced(case, std, est, ref_terms, combo, target)
            if terms is not None:
                rows.append((cost(case, terms, std, est), terms))
    rows = [r for r in rows if math.isfinite(r[0])]
    if not rows:
        return None
    low = min(c for c, _ in rows)
    ties = [(c, t) for c, t in rows if c <= low + max(1.0, abs(low) * 1e-5)]
    c, terms = max(ties, key=lambda r: friendliness(r[1]))
    return terms, c


def break_even(case, terms, std=None, est=None):
    """The highest price at `terms` whose C is still below the alternative's, or None when there
    is no alternative; 0.0 when no positive price beats it. C is convex in the price (the
    relationship charge falls as it rises), so the search starts at C's minimum."""
    ceiling = c_alt(case)
    if math.isinf(ceiling):
        return None

    def c(price):
        return cost(case, at_price(terms, price), std, est)

    hi = max(1.0, terms.get("unit_price") or 1.0) * 3
    a, b = 0.01, hi
    for _ in range(80):
        m1, m2 = a + (b - a) / 3, b - (b - a) / 3
        a, b = (a, m2) if c(m1) <= c(m2) else (m1, b)
    lo = (a + b) / 2
    if not c(lo) < ceiling:
        return 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if c(mid) < ceiling else (lo, mid)
    return max(0.0, round(math.floor(lo * 100) / 100 - 0.01, 2))


def relationship_optimum(case, terms, std, est, lo, hi):
    """Relationship goals: the price in [lo, hi] with the lowest C, relationship charge included.
    None outside relationship goals or without a full-cost estimate."""
    if case["buyer"].get("category_goal") not in RELATIONSHIP_GOALS or not est.get("full_cost") or hi <= lo:
        return None
    best_p, best_c = lo, math.inf
    for i in range(201):
        p = lo + (hi - lo) * i / 200
        c = cost(case, at_price(terms, p), std, est)
        if c < best_c - 1e-6:
            best_p, best_c = p, c
    return round(best_p, 2)


def quoted_offer(case, terms, std=None):
    """A supplier's package as a valid outgoing sheet, exactly as it would close, or None when it
    cannot be sent (a value outside the options, a fixed term changed, a must-have broken)."""
    effective = closing_terms(case, terms, std)
    if any(effective.get(k) != v for k, v in case.get("fixed_terms", {}).items()):
        return None
    offer = {k: effective.get(k) for k in case["negotiable"]}
    price = offer.get("unit_price")
    if not isinstance(price, int | float) or not math.isfinite(price) or price <= 0:
        return None
    opts = case.get("term_options", {})
    if any(opts.get(k) and v not in opts[k] for k, v in offer.items() if k != "unit_price"):
        return None
    try:
        return offer if must_haves_ok(case, offer, std) else None
    except (KeyError, TypeError):
        return None


def cost_curve(case, terms, std, est, lo, hi, n=120):
    """C at `terms` on a price grid [lo, hi]: the planner scores outcomes in euros, not in price,
    so a relationship charge below the sustainable margin counts as it will."""
    grid = [lo + (hi - lo) * i / n for i in range(n + 1)]
    return grid, [cost(case, at_price(terms, p), std, est) for p in grid]


def interp(grid, values, x):
    if x <= grid[0]:
        return values[0]
    if x >= grid[-1]:
        return values[-1]
    step = grid[1] - grid[0]
    i = min(len(grid) - 2, int((x - grid[0]) / step))
    w = (x - grid[i]) / step
    return values[i] * (1 - w) + values[i + 1] * w


__all__ = [
    "RELATIONSHIP_GOALS",
    "acceptable",
    "at_price",
    "break_even",
    "c_alt",
    "complete_offer",
    "convert",
    "cost",
    "estimate",
    "must_haves_ok",
    "plan_terms",
    "price_for_value",
    "quoted_offer",
    "relationship_optimum",
    "value",
]
