"""The menu: every valid term package, priced for us and estimated for the supplier (code only).

The committee never invents a term sheet. Code enumerates the allowed non-price combinations,
picks the one that costs us least as the main package, and prices every other combination at the
price that would cost us exactly the same ("iso-cost"). What differs between them is what each is
worth to the supplier: paying sooner is worth more to a supplier short of cash, a longer contract
to one with idle machines. A package that costs us the same and is worth more to the supplier is a
free trade - those make the menu, ranked by what they give the supplier.

The supplier side uses the kit's own `supplier_value`, fed with our estimate of the supplier: its
cost of money (owner type, cash signals), its full cost (should-cost data) and its standard terms
(quote PDF).
"""

import itertools
import math

from arena_kit.cost import RELATIONSHIP_GOALS, complete_offer, must_haves_ok, supplier_value, total_cost

# cost of money per year by owner type; a supplier short of cash values money now much more
RATE = {"group": 0.05, "private": 0.07, "family": 0.075, "founder": 0.13}
CASH_PREMIUM = 0.07
# full cost of other countries against the German should-cost base (company.md)
COUNTRY = {"DE": 1.0, "AT": 1.05, "CH": 1.1, "CZ": 0.82, "PL": 0.82, "HU": 0.82, "SK": 0.82, "TR": 0.75, "CN": 0.7}
MIN_MARGIN = 0.02  # a supplier sells at least this far above its full cost
MAX_ITEMS = 6


def estimate(case, std, dossier, quote, pkg=None):
    """Our estimate of the supplier for `supplier_value` and `relationship_cost`."""
    prof = (dossier or {}).get("profile") or {}
    needs = (dossier or {}).get("needs") or {}
    rate = RATE.get(prof.get("owner_type"), 0.07) + (CASH_PREMIUM if "cash" in needs else 0.0)
    if prof.get("skonto"):
        pct, days = prof["skonto"]
        if pct and days:  # a cash discount of pct % for paying (30 - days) days early
            rate = max(rate, min(0.25, pct / 100 * 360 / max(10, 30 - days)))
    est = {"standard": {**(quote or {}), **(std or {})}, "interest_rate": round(rate, 3)}
    full = full_cost(case, dossier, pkg)
    if full:
        est["full_cost"] = full
    return est


def full_cost(case, dossier, pkg):
    """Full cost per unit from the should-cost data, or None. A should-cost of this very supplier's
    plant is taken as is; a generic German one is moved by the supplier's country."""
    parts = case.get("parts") or []
    if pkg is None or len(parts) != 1:
        return None
    try:
        rows = pkg.query("SELECT supplier_id, unit_cost, cost_base FROM should_cost WHERE part_id = ?", (parts[0],))
    except Exception:  # noqa: BLE001
        return None
    sid = (dossier or {}).get("supplier_id")
    own = [r for r in rows if r.get("supplier_id") == sid]
    if own:
        return float(own[0]["unit_cost"])
    if rows:
        country = ((dossier or {}).get("profile") or {}).get("country")
        return float(rows[0]["unit_cost"]) * COUNTRY.get(country, 1.0)
    return None


def cost(case, terms, std, est=None):
    """C for us; with `est`, including the relationship charge of strategic goals."""
    use = est if est and est.get("full_cost") and case["buyer"].get("category_goal") in RELATIONSHIP_GOALS else None
    return total_cost(case, terms, std, use)


def worth(case, terms, est):
    """What a package is worth to the supplier per unit, in cash at its standard terms."""
    try:
        return supplier_value(case, terms, est)
    except Exception:  # noqa: BLE001 - an odd field: no estimate
        return None


def combos(case):
    """Every allowed combination of the negotiable fields other than the price."""
    fields = [f for f in case["negotiable"] if f != "unit_price" and case.get("term_options", {}).get(f)]
    options = [case["term_options"][f] for f in fields]
    for values in itertools.product(*options):
        yield dict(zip(fields, values, strict=True))


def at_price(case, terms, price):
    return complete_offer(case, {**terms, "unit_price": round(price, 2)})


def price_for_cost(case, terms, target, std):
    """The price at which `terms` cost us `target` (C is linear in the price without the
    relationship term)."""
    a, b = 1.0, 2.0
    ca, cb = total_cost(case, {**terms, "unit_price": a}, std), total_cost(case, {**terms, "unit_price": b}, std)
    if not (math.isfinite(ca) and math.isfinite(cb)) or cb == ca:
        return None
    return a + (target - ca) * (b - a) / (cb - ca)


def describe(case, terms, base=None):
    """The fields of `terms` that differ from `base`, in words."""
    out = []
    for f in case["negotiable"]:
        if f == "unit_price" or (base is not None and terms.get(f) == base.get(f)):
            continue
        v = terms.get(f)
        if f == "index_clause":
            v = "none" if not v else f"{v['series']} clause, deadband {v['deadband_pct']}%"
        out.append(f"{f}={v}")
    return ", ".join(out)


def base_terms(case, std, price):
    """The non-price package that costs us least at `price`, keeping every must-have."""
    best = None
    for combo in combos(case) or [{}]:
        t = at_price(case, combo, price)
        if not must_haves_ok(case, t, std):
            continue
        c = total_cost(case, t, std)
        if best is None or c < best[0]:
            best = (c, t)
    return best[1] if best else at_price(case, {}, price)


def security(case, terms, main):
    """What a package gives the supplier that is not cash (the kit's supplier value leaves these
    out, but suppliers still want them): a longer commitment, more binding volume, a price that
    follows its material index, more time to deliver."""
    out = []
    if (terms.get("contract_months") or 0) > (main.get("contract_months") or 0):
        out.append("longer commitment")
    if (terms.get("volume_per_year") or 0) > (main.get("volume_per_year") or 0):
        out.append("more binding volume")
    if terms.get("index_clause") and not main.get("index_clause"):
        out.append("price follows its material index")
    elif (
        terms.get("index_clause")
        and main.get("index_clause")
        and (terms["index_clause"]["deadband_pct"] < main["index_clause"]["deadband_pct"])
    ):
        out.append("index clause with a smaller deadband")
    if (terms.get("lead_time_days") or 0) > (main.get("lead_time_days") or 0):
        out.append("more time to deliver")
    return out


def build(case, std, est, main):
    """The menu around our main package `main` (complete terms with its price): M0 is `main`, then
    up to MAX_ITEMS - 1 packages that cost us exactly the same and give the supplier more - cash
    (paid sooner, a later start of the new price) or security (commitment, volume, an index clause).
    Simple trades first: a package that changes two fields must give more than its one-field parts."""
    c_main = total_cost(case, main, std)
    v_main = worth(case, main, est)
    items = [
        {
            "id": "M0",
            "terms": main,
            "label": "",
            "cost_us": round(c_main),
            "cash_for_supplier": 0.0,
            "security_for_supplier": [],
        }
    ]
    seen = []
    for combo in combos(case):
        t = {**main, **combo}
        changed = [f for f in combo if combo[f] != main.get(f)]
        if not changed or len(changed) > 2:
            continue
        p = price_for_cost(case, t, c_main, std)
        if p is None or p <= 0:
            continue
        t = at_price(case, t, p)
        if not must_haves_ok(case, t, std):
            continue
        v = worth(case, t, est)
        cash = (v - v_main) if v is not None and v_main is not None else 0.0
        sec = security(case, t, main)
        if cash <= 0.002 * main["unit_price"] and not sec:
            continue
        # rank: cash per unit as a share of the price, security as a fixed bonus each
        seen.append((cash / main["unit_price"] + 0.004 * len(sec), len(changed), cash, sec, t))
    seen.sort(key=lambda g: (-g[0], g[1]))
    for _score, _n, cash, sec, t in seen:
        if len(items) >= MAX_ITEMS:
            break
        label = describe(case, t, main)
        simpler = [i for i in items[1:] if set(i["label"].split(", ")) < set(label.split(", "))]
        if any(i["label"] == label for i in items) or any(
            s["cash_for_supplier"] >= cash - 0.01 * abs(cash) for s in simpler
        ):
            continue
        items.append(
            {
                "id": f"M{len(items)}",
                "terms": t,
                "label": label,
                "cost_us": round(total_cost(case, t, std)),
                "cash_for_supplier": round(cash, 2),
                "security_for_supplier": sec,
            }
        )
    return items


def text(case, items):
    """The menu for the committee: no hidden figures, just what each package is."""
    lines = []
    for i in items:
        extra = f"; security for it: {', '.join(i['security_for_supplier'])}" if i["security_for_supplier"] else ""
        lines.append(
            f"{i['id']}: unit_price {i['terms']['unit_price']} with {i['label'] or 'our main terms'} "
            f"({describe(case, i['terms'])}) | same cost to us | cash for the supplier vs M0: "
            f"{i['cash_for_supplier']:+.2f} EUR per unit{extra}"
        )
    return "\n".join(lines)


def break_even(case, terms, std, est):
    """The highest price at `terms` that still beats our alternative (None without one)."""
    alt = case["buyer"].get("alternative") or {}
    if alt.get("kind") in (None, "none") or not alt.get("terms", {}).get("unit_price"):
        return None
    c_alt = total_cost(case, alt["terms"])
    return price_for_cost(case, terms, c_alt, std)


def relationship_optimum(case, terms, std, est, lo, hi):
    """Strategic goals: the price with the lowest C once the relationship charge counts."""
    if case["buyer"].get("category_goal") not in RELATIONSHIP_GOALS or not est.get("full_cost") or hi <= lo:
        return None
    best = None
    for k in range(81):
        p = lo + (hi - lo) * k / 80
        c = cost(case, at_price(case, terms, p), std, est)
        if best is None or c < best[0]:
            best = (c, p)
    return best[1]
