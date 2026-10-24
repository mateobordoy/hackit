"""The evidence locker: arguments the arena can verify, each one sentence with its figure and IDs.

Code writes every figure here, straight from the data the arena checks against (`facts.json`), so a
claim is never `contradicted`. The committee only chooses which claims to use and in what order.
Rules from the API: at most 3 checks per message, each ID once per case (a rival's offer may go to
every other bidder), an index window of at most 12 months that ends within the data's last 12.
Figures are written without thousands separators ("2637", not "2,637").
"""

import logging
import re

log = logging.getLogger("hackit")


def _fmt(x):
    return f"{x:.2f}"


def _months(m):
    y, mo = m.split("-")
    return int(y) * 12 + int(mo) - 1


def _q(pkg, sql, params=(), limit=50):
    try:
        return pkg.query(sql, params, limit)
    except Exception:  # noqa: BLE001
        return []


def index_moves(pkg, part):
    """[(weight, series, best falling window)] for the part's cost drivers."""
    out = []
    for d in _q(pkg, "SELECT series_id, weight FROM cost_drivers WHERE part_id = ?", (part,)):
        vals = _q(
            pkg, "SELECT id, month, value FROM index_values WHERE series_id = ? ORDER BY month", (d["series_id"],), 500
        )
        if len(vals) < 2:
            continue
        last = _months(vals[-1]["month"])
        best = None
        for j, b in enumerate(vals):
            if _months(b["month"]) <= last - 12:
                continue
            for a in vals[:j]:
                gap = _months(b["month"]) - _months(a["month"])
                if 0 < gap <= 12 and a["value"]:
                    ch = (b["value"] / a["value"] - 1) * 100
                    if best is None or ch < best[0]:
                        best = (ch, a, b)
        twelve = [v for v in vals if _months(v["month"]) == last - 12]
        change_12m = (vals[-1]["value"] / twelve[0]["value"] - 1) * 100 if twelve and twelve[0]["value"] else None
        out.append(
            {"series": d["series_id"], "weight": d["weight"], "best": best, "change_12m": change_12m, "last": vals[-1]}
        )
    return out


def justified_change(moves):
    """The cost change the drivers justify over 12 months, weighted, in % (None without data)."""
    known = [m for m in moves if m["change_12m"] is not None]
    if not known:
        return None
    return sum(m["weight"] * m["change_12m"] for m in known)


def locker(pkg, case, sid, quote_price=None):
    """Every verifiable argument for supplier `sid` in this case, strongest first. `quote_price`: its
    opening price - a figure above it would argue its case, not ours, and is left out."""
    claims = []
    if pkg is None:
        return claims
    parts = case.get("parts") or []
    part = parts[0] if parts else None
    facts = {**(getattr(pkg, "facts", {}) or {}), **(case.get("facts") or {})}

    def add(kind, text, refs, strength):
        claims.append(
            {"id": f"{kind}:{len(claims)}", "kind": kind, "text": text, "refs": refs, "strength": round(strength, 2)}
        )

    # 1. cost drivers that fell
    if part:
        for m in index_moves(pkg, part):
            if m["best"] and m["best"][0] < -0.5:
                ch, a, b = m["best"]
                add(
                    "index",
                    f"The {m['series']} index, one of this part's cost drivers, changed {ch:+.1f}% from "
                    f"{a['month']} to {b['month']} ({a['id']}, {b['id']}).",
                    [a["id"], b["id"]],
                    m["weight"] * abs(ch),
                )
    # 2. the letter gives less notice than the contract requires
    doc = facts.get(case.get("trigger_doc") or "") or {}
    if doc.get("days"):
        for c in _q(pkg, "SELECT id, notice_days FROM contracts WHERE supplier_id = ?", (sid,), 3):
            ctr = facts.get(c["id"]) or {}
            if ctr.get("days") and doc["days"] < ctr["days"]:
                add(
                    "notice",
                    f"Your letter gives {doc['days']} days' notice ({case['trigger_doc']}), while our "
                    f"framework agreement requires {ctr['days']} days ({c['id']}).",
                    [case["trigger_doc"], c["id"]],
                    8.0,
                )
    # 3. our should-cost
    if part and (cost := facts.get(f"cost:{part.split(':')[1]}")):
        add(
            "should_cost",
            f"Our should-cost estimate for this part is EUR {_fmt(cost['unit_cost'])} per unit "
            f"(cost:{part.split(':')[1]}).",
            [f"cost:{part.split(':')[1]}"],
            6.0,
        )
    # 4. its delivery and quality record
    sc = _q(pkg, "SELECT id FROM scorecards WHERE supplier_id = ? ORDER BY quarter DESC", (sid,), 1)
    if sc and (row := facts.get(sc[0]["id"])):
        if row.get("otd_pct") is not None and row["otd_pct"] < 92:
            add(
                "delivery",
                f"Your on-time delivery to us was {row['otd_pct']:.1f}% over the last 12 months "
                f"({sc[0]['id']}), against our target of 95%.",
                [sc[0]["id"]],
                (95 - row["otd_pct"]) / 2,
            )
        if row.get("ppm") and row["ppm"] > 800:
            add(
                "quality",
                f"Your defect rate with us was {row['ppm']:.0f} ppm over the last 12 months ({sc[0]['id']}).",
                [sc[0]["id"]],
                min(row["ppm"] / 500, 8),
            )
    # 5. our growing forecast: more volume for it
    if part:
        fcs = sorted(k for k in facts if k.startswith(f"fc:{part.split(':')[1]}@"))
        if fcs and (fc := facts[fcs[-1]]).get("change_pct") and fc["change_pct"] > 2:
            add(
                "forecast",
                f"Our latest demand forecast for this part is up {fc['change_pct']:.1f}% ({fcs[-1]}).",
                [fcs[-1]],
                2.0,
            )
    # 6. what we have been paying it
    if part:
        pos = _q(
            pkg,
            "SELECT id FROM po_lines WHERE part_id = ? AND supplier_id = ? ORDER BY order_date DESC",
            (part, sid),
            3,
        )
        pos = [p["id"] for p in pos if facts.get(p["id"])]
        if len(pos) >= 2:
            price = facts[pos[0]]["unit_price"]
            add(
                "orders",
                f"Our recent orders of this part were placed at EUR {_fmt(price)} per unit ({', '.join(pos[:2])}).",
                pos[:2],
                1.5,
            )
    # 7. our alternative, when it is a quote
    alt = case["buyer"].get("alternative") or {}
    if alt.get("kind") == "quote" and alt.get("ref") and (q := facts.get(alt["ref"])) and q.get("unit_price"):
        add(
            "alternative",
            f"We hold another quote for this part at EUR {_fmt(q['unit_price'])} per unit ({alt['ref']}).",
            [alt["ref"]],
            5.0,
        )
    if quote_price:

        def helps(c):
            if c["kind"] not in ("should_cost", "alternative", "orders"):
                return True
            eur = float(re.search(r"EUR ([\d.]+)", c["text"]).group(1))
            return eur < quote_price * (0.99 if c["kind"] == "should_cost" else 1.0)

        claims = [c for c in claims if helps(c)]
    claims.sort(key=lambda c: -c["strength"])
    return claims


def rival(case, threads, sid, standing):
    """The cheapest other bidder's standing offer as a claim (RFQs), or None."""
    best = None
    for other, events in threads.items():
        if other == sid:
            continue
        oid, terms = standing(events)
        if oid and terms.get("unit_price") and (best is None or terms["unit_price"] < best[1]):
            best = (oid, terms["unit_price"])
    if not best:
        return None
    return {
        "id": f"rival:{best[0]}",
        "kind": "rival",
        "refs": [best[0]],
        "strength": 9.0,
        "text": f"Another bidder currently offers EUR {_fmt(best[1])} per unit ({best[0]}).",
    }
