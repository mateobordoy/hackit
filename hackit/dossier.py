"""Phase 0: the dossier of a supplier, from the data package only (no model, no clock).

A dossier answers "who is this supplier and what does it need right now": its company profile,
how we treat it (do we pay late?), how it performs, how it negotiated before, what its contract
says - and its *needs*, read from the notes, letters, news and quote remarks. The generated texts
reuse a small set of phrasings and the news carry a topic, so each need is a list of patterns plus
news topics. Every need found keeps the evidence (document ID and sentence), so the committee can
read it and a replay can show where it came from.

Unknown suppliers (not in the package) get an empty dossier; their own e-mails are scanned with the
same patterns as they arrive (`needs_in`).
"""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("hackit")

# need -> (patterns in notes, letters, remarks and e-mails; news topics; what it means for us)
NEEDS = {
    "cash": (
        r"pay(ing)? (us )?earlier|bank is watching|pay our invoices within|cash and the banks|short hours"
        r"|short-time work|payment within \d+ days|cash is tight|liquidity|faster payment|cash flow"
        r"|restructur\w+|insolven\w+|credit (agency|insurer)|lowers? (its|their) rating",
        {"credit_watch", "credit_downgrade", "restructuring", "short_time_work"},
        "short of cash: paying sooner is worth more to it than it costs us",
    ),
    "idle_capacity": (
        r"second shift has been on hold|take additional parts|shop floor was idle|looks for new customers"
        r"|free (machine )?capacity|keep (the|our) machines (busy|running)",
        {"free_capacity"},
        "idle machines: volume and a longer commitment are worth a lot to it",
    ),
    "full_capacity": (
        r"order intake is very strong|cannot confirm your full forecast|fully booked|high capacity utili[sz]ation"
        r"|prioriti[sz]e customers with firm|allocat\w+ volumes",
        {"full_order_book", "allocation", "big_order"},
        "fully booked: it charges for speed and volume; a firm commitment and a relaxed lead time help",
    ),
    "price_frozen": (
        r"discounts not possible|not discounted|price list applies to all|list conditions|head-office directive"
        r"|no room on price|prices? (are|will be) set centrally",
        set(),
        "list price does not move: negotiate payment, logistics, contract terms instead",
    ),
    "approval_ladder": (
        r"pricing committee|need the approval",
        set(),
        "bigger price moves need a committee: small steady steps, no big jumps",
    ),
    "stale_costing": (
        r"own spreadsheet|not followed (his|her|its|their) material|office manager does the costing",
        set(),
        "its costing is out of date: current index figures can disarm it",
    ),
    "open_book": (
        r"walk (us|you) through (their|our|his) calculation",
        set(),
        "offered to open its calculation: a should-cost argument lands well",
    ),
    "hedged": (
        r"covered at fixed prices|material covered until|secures power prices|bought .{1,60} forward|hedged",
        {"energy_contract"},
        "its material is hedged: market moves do not reach its costs yet; an index clause is worth little to it",
    ),
    "partnership": (
        r"kept supplying at the old price|expects partnership",
        set(),
        "remembers favours: acknowledge the relationship, avoid squeezing",
    ),
    "values_us": (
        r"important reference customer",
        set(),
        "we are a reference customer for it: it wants to keep us",
    ),
    "deprioritising_us": (
        r"reviewing small accounts|not strategic|reorganising our range|supply from stock only"
        r"|individual agreements will not be extended|focus on customers with volume",
        {"refocus"},
        "we matter little to it: only volume or a multi-year agreement moves it",
    ),
    "late_payment_grievance": (
        r"invoices are overdue|keep to the agreed payment terms",
        set(),
        "we have paid it late: paying on time is a real concession",
    ),
    "supply_risk": (
        r"longer delivery times|lead times are currently longer|shortage",
        {"chip_shortage", "succession", "new_owner"},
        "supply risk on its side: secure the volume, do not push it to walk",
    ),
}
# routine mail that never says anything about a negotiation
NOISE = re.compile(
    r"holiday|plant is closed|iso \d+ certificate|new colleague|returnable boxes|supplier fair|booth", re.I
)


def needs_in(text, source=None):
    """{need: [evidence]} found in one text (a note, a letter, a supplier e-mail)."""
    found = {}
    for need, (pattern, _, _) in NEEDS.items():
        m = re.search(pattern, text or "", re.I)
        if m:
            start = text.rfind(".", 0, m.start()) + 1
            end = text.find(".", m.end())
            sentence = " ".join(text[start : end if end > 0 else len(text)].split())[:240]
            found.setdefault(need, []).append({"source": source, "text": sentence})
    return found


def _merge(into, found):
    for need, ev in found.items():
        into.setdefault(need, []).extend(ev)


def _q(pkg, sql, params=(), limit=50):
    try:
        return pkg.query(sql, params, limit)
    except Exception:  # noqa: BLE001 - a package without this table: the dossier knows less
        return []


def _one(rows):
    return rows[0] if rows else {}


def build(pkg, sid, parts=()):
    """The dossier of supplier `sid` for the case's parts. Never raises: missing data stays empty."""
    d = {"supplier_id": sid, "known": False, "needs": {}, "documents": [], "news": []}
    if pkg is None:
        return d
    sup = _one(_q(pkg, "SELECT * FROM suppliers WHERE id = ?", (sid,)))
    d["known"] = bool(sup) or bool(pkg.lookup(sid))
    if not d["known"]:
        return d
    intel = _one(_q(pkg, "SELECT * FROM supplier_intel WHERE supplier_id = ?", (sid,)))
    d["profile"] = {
        "name": sup.get("name"),
        "country": sup.get("country"),
        "category": sup.get("category"),
        "owner_type": intel.get("owner_type"),
        "staff": intel.get("staff"),
        "turnover_eur": intel.get("turnover_eur") or intel.get("turnover_band"),
        "equity_ratio_pct": intel.get("equity_ratio_pct"),
        "credit_index": intel.get("credit_index"),
        "credit_index_12m_ago": intel.get("credit_index_12m_ago"),
        "group_parent": intel.get("group_parent"),
        "standard_payment_days": sup.get("payment_days"),
        "skonto": (sup.get("skonto_pct"), sup.get("skonto_days")) if sup.get("skonto_pct") else None,
    }
    # how we treat it
    late = _one(
        _q(
            pkg,
            "SELECT round(avg(julianday(paid_date) - julianday(due_date)), 1) AS days_late, count(*) AS n "
            "FROM invoices WHERE supplier_id = ? AND paid_date IS NOT NULL",
            (sid,),
        )
    )
    spend = _q(
        pkg,
        "SELECT fiscal_year, round(sum(spend_eur)) AS eur FROM spend_cube WHERE supplier_id = ? "
        "GROUP BY fiscal_year ORDER BY fiscal_year DESC",
        (sid,),
        3,
    )
    d["relationship"] = {"we_pay_days_late": late.get("days_late"), "spend_by_year": spend}
    turnover = intel.get("turnover_eur")
    if turnover and spend:
        d["relationship"]["our_share_of_its_turnover_pct"] = round(100 * spend[0]["eur"] / turnover, 1)
    # how it performs
    sc = _q(
        pkg,
        "SELECT id, quarter, otd_pct, ppm, colour, open_8d_overdue FROM scorecards "
        "WHERE supplier_id = ? ORDER BY quarter DESC",
        (sid,),
        4,
    )
    d["performance"] = {"scorecards": sc}
    # how it negotiated before
    hist = _q(
        pkg,
        "SELECT date, type, part_id, asked_pct, settled_pct, note FROM negotiation_history "
        "WHERE supplier_id = ? ORDER BY date DESC",
        (sid,),
        12,
    )
    asked = [h["asked_pct"] for h in hist if h.get("asked_pct") is not None]
    settled = [h["settled_pct"] for h in hist if h.get("settled_pct") is not None]
    d["history"] = {
        "events": hist[:6],
        "mean_asked_pct": round(sum(asked) / len(asked), 2) if asked else None,
        "mean_settled_pct": round(sum(settled) / len(settled), 2) if settled else None,
        "below_cost_before": any("below full cost" in (h.get("note") or "") for h in hist),
    }
    ctr = _q(
        pkg,
        "SELECT id, valid_from, valid_to, payment_days, incoterm, notice_days, price_basis "
        "FROM contracts WHERE supplier_id = ?",
        (sid,),
        3,
    )
    clauses = _q(
        pkg,
        "SELECT cl.id, cl.type, cl.series, cl.material_share, cl.deadband_pct, cl.days "
        "FROM contract_clauses cl JOIN contracts c ON c.id = cl.contract_id WHERE c.supplier_id = ? "
        "AND cl.type NOT IN ('scope')",
        (sid,),
        12,
    )
    d["contract"] = {"contracts": ctr, "clauses": clauses}
    # its words: notes, letters, news, quote remarks
    for ref in pkg.ids("doc:"):
        row = pkg.lookup(ref) or {}
        if sid not in (row.get("supplier"), row.get("supplier_id")):
            continue
        try:
            text = pkg.document(ref)
        except Exception:  # noqa: BLE001
            text = row.get("title") or ""
        body = "\n".join(line for line in text.splitlines()[3:] if line.strip()) or text
        noise = bool(NOISE.search(body))
        d["documents"].append(
            {
                "id": ref,
                "date": row.get("date"),
                "kind": row.get("type"),
                "title": row.get("title"),
                "text": body[:600],
                "noise": noise,
            }
        )
        if not noise:
            _merge(d["needs"], needs_in(body, ref))
    for ref in pkg.ids("news:"):
        row = pkg.lookup(ref) or {}
        if row.get("supplier") != sid and row.get("supplier_id") != sid:
            continue
        topic = row.get("topic")
        d["news"].append({"id": ref, "date": row.get("date"), "topic": topic, "headline": row.get("headline")})
        for need, (_, topics, _) in NEEDS.items():
            if topic in topics:
                d["needs"].setdefault(need, []).append({"source": ref, "text": row.get("headline")})
        _merge(d["needs"], needs_in(row.get("headline") or "", ref))
    for q in _q(pkg, "SELECT id, remarks FROM quotes WHERE supplier_id = ? AND remarks != ''", (sid,), 10):
        _merge(d["needs"], needs_in(q["remarks"], q["id"]))
    # figures that point to a need
    p = d["profile"]
    if p.get("credit_index") and p.get("credit_index_12m_ago") and p["credit_index"] > p["credit_index_12m_ago"] + 15:
        d["needs"].setdefault("cash", []).append(
            {"source": sid, "text": f"credit index worsened {p['credit_index_12m_ago']} -> {p['credit_index']}"}
        )
    if (late.get("days_late") or 0) > 10:
        d["needs"].setdefault("late_payment_grievance", []).append(
            {"source": "invoices", "text": f"we pay it {late['days_late']} days late on average"}
        )
    d["documents"].sort(key=lambda x: x.get("date") or "", reverse=True)
    d["news"].sort(key=lambda x: x.get("date") or "", reverse=True)
    return d


def summary(d):
    """The dossier as compact text for a model: needs first, then the facts."""
    if not d.get("known"):
        return f"{d['supplier_id']}: not in our data. Read it from its own e-mails."
    lines = [f"{d['supplier_id']} {json.dumps(d.get('profile'), ensure_ascii=False, default=str)}"]
    for need, ev in d["needs"].items():
        lines.append(f"NEED {need} ({NEEDS[need][2]}): " + " | ".join(f"{e['source']}: {e['text']}" for e in ev[:3]))
    lines.append(f"RELATIONSHIP {json.dumps(d.get('relationship'), default=str)}")
    lines.append(f"PERFORMANCE {json.dumps(d.get('performance'), default=str)}")
    lines.append(f"HISTORY {json.dumps(d.get('history'), ensure_ascii=False, default=str)}")
    lines.append(f"CONTRACT {json.dumps(d.get('contract'), default=str)}")
    for n in d["news"][:4]:
        lines.append(f"NEWS {n['id']} {n['date']} [{n['topic']}] {n['headline']}")
    for doc in [x for x in d["documents"] if not x["noise"]][:6]:
        lines.append(f"DOC {doc['id']} {doc['date']} {doc['kind']}: {doc['text'][:300]}")
    return "\n".join(lines)


def build_all(pkg, out):
    """Phase 0 for every supplier the package details, written to `out` (JSON)."""
    sids = [r["id"] for r in _q(pkg, "SELECT id FROM suppliers WHERE detail = 'full'", (), 1000)]
    data = {sid: build(pkg, sid) for sid in sids}
    Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return data
