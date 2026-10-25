"""Build buyer/dossier.json: one entry per fully documented supplier (`detail = full`).

    python scripts/build_dossier.py            # needs the data package (+ Sunday release) and a model key

Code computes every figure from the package tables; the reader agent reads every text about the
supplier (letters, call and meeting notes, news, quote remarks, the notes of past negotiations) and
labels its situation, each label with a verbatim quote; the phrase lists run on the same texts.
A quote that is not in the texts drops its label (reader.validate). Run it again after any data
change; review the output before the code freeze (fields `review.status`, `review.note`).
"""

import json
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buyer import config, reader, supplier  # noqa: E402
from buyer.datapkg import CaseData, load_package  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "buyer" / "dossier.json"
AS_OF = date(2026, 9, 30)  # the package's last booking day (invoices, receipts, notifications)
YEAR_AGO = date(2025, 10, 1)
LATE_DAYS = 8  # mean days late over the year's paid invoices that marks "we pay it late"
FIELDS = {
    "reading": "labels the agent absorbs at setup (reader agent + phrase lists), each with quote and source",
    "company": "register facts: owner, size, equity, credit index (higher = riskier) now and a year ago",
    "terms": "its standard terms (suppliers, contracts)",
    "our_payments": "how we paid its invoices in the last 12 months: mean days late, share paid late, we_pay_late flag",
    "quality": "latest scorecard and supplier-caused quality notifications in the last 12 months",
    "dependency": "parts it supplies to us and where it is our only active source",
    "costing": "should-cost coverage and past negotiations (context only: they do not predict where it stops)",
    "review": "for the team: status auto|checked|corrected and a note",
}


def d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def one(rows):
    return rows[0] if rows else {}


def facts(data, sid):
    q = data.query
    sup = one(q("SELECT * FROM suppliers WHERE id = ?", (sid,)))
    intel = one(q("SELECT * FROM supplier_intel WHERE supplier_id = ?", (sid,)))
    ctr = one(q("SELECT * FROM contracts WHERE supplier_id = ?", (sid,)))
    ci, ci0 = intel.get("credit_index"), intel.get("credit_index_12m_ago")
    trend = None if ci is None or ci0 is None else ("worse" if ci > ci0 else "better" if ci < ci0 else "same")

    paid, late_days, open_overdue = 0, [], 0
    for inv in q("SELECT due_date, paid_date FROM invoices WHERE supplier_id = ?", (sid,)):
        due, pd = d(inv.get("due_date")), d(inv.get("paid_date"))
        if pd and due and pd >= YEAR_AGO:
            paid += 1
            late_days.append(max(0, (pd - due).days))
        elif not pd and due and due < AS_OF:
            open_overdue += 1
    late = [x for x in late_days if x > 0]

    sc = one(q("SELECT * FROM scorecards WHERE supplier_id = ? ORDER BY quarter DESC LIMIT 1", (sid,)))
    qns = [
        r
        for r in q("SELECT qn_date, supplier_caused, defect FROM quality_notifications WHERE supplier_id = ?", (sid,))
        if d(r.get("qn_date")) and d(r.get("qn_date")) >= YEAR_AGO
    ]
    parts = q("SELECT part_id, status, quota_pct FROM sourcing WHERE supplier_id = ?", (sid,))
    single = []
    for p in parts:
        if p.get("status") != "active":
            continue
        others = q(
            "SELECT supplier_id FROM sourcing WHERE part_id = ? AND status = 'active' AND supplier_id != ?",
            (p["part_id"], sid),
        )
        if not others:
            single.append(p["part_id"])
    should = q("SELECT id, part_id FROM should_cost WHERE supplier_id = ? OR supplier_id IS NULL", (sid,))
    own_parts = {p["part_id"] for p in parts}
    hist = q(
        "SELECT event_id, date, part_id, type, asked_pct, settled_pct, note FROM negotiation_history "
        "WHERE supplier_id = ? ORDER BY date",
        (sid,),
    )
    notes = sorted({h["note"] for h in hist if h.get("note")})
    return {
        "name": sup.get("name"),
        "country": sup.get("country"),
        "category": sup.get("category"),
        "abc_class": sup.get("abc_class"),
        "company": {
            "owner_type": intel.get("owner_type"),
            "group_parent": intel.get("group_parent"),
            "staff": intel.get("staff"),
            "turnover_band": intel.get("turnover_band"),
            "equity_ratio_pct": intel.get("equity_ratio_pct"),
            "credit_index": ci,
            "credit_index_12m_ago": ci0,
            "credit_trend": trend,
            "audit_grade_vda63": intel.get("vda63_grade"),
        },
        "terms": {
            "payment_days": sup.get("payment_days"),
            "cash_discount": (
                f"{sup['skonto_pct']}% within {sup['skonto_days']} days" if sup.get("skonto_pct") else None
            ),
            "incoterm": sup.get("incoterm"),
            "standard_lead_time_days": sup.get("standard_lead_time_days"),
            "contract": {
                "id": ctr.get("id"),
                "valid_to": ctr.get("valid_to"),
                "payment_days": ctr.get("payment_days"),
                "notice_days": ctr.get("notice_days"),
                "price_basis": ctr.get("price_basis"),
            }
            if ctr
            else None,
        },
        "our_payments": {
            "invoices_paid_12m": paid,
            "mean_days_late": round(statistics.fmean(late_days), 1) if late_days else 0,
            "paid_late_share": round(len(late) / paid, 3) if paid else None,
            # we pay almost everyone a few days late; ten days or more stands out (ProcureCabana:
            # Saalach 19, Waelzhammer 16, Lindlbauer 13 days; Kalwisz "we often see late payments")
            "we_pay_late": bool(late_days) and statistics.fmean(late_days) >= LATE_DAYS,
            "open_overdue_invoices": open_overdue,
        },
        "quality": {
            "scorecard": {k: sc.get(k) for k in ("id", "quarter", "otd_pct", "ppm", "score", "colour")} if sc else None,
            "notifications_12m": len(qns),
            "supplier_caused_12m": sum(1 for r in qns if r.get("supplier_caused")),
        },
        "dependency": {
            "parts": [{k: p.get(k) for k in ("part_id", "status", "quota_pct")} for p in parts],
            "only_active_source_for": single,
        },
        "costing": {
            "should_cost": sorted(s["id"] for s in should if s.get("part_id") in own_parts),
            "sold_below_full_cost": any("below full cost" in n for n in notes),
            "history_notes": notes,
            "history": [
                {k: h.get(k) for k in ("event_id", "date", "part_id", "type", "asked_pct", "settled_pct")} for h in hist
            ],
        },
    }


def history_texts(data, sid):
    rows = data.query("SELECT event_id, date, type, note FROM negotiation_history WHERE supplier_id = ?", (sid,))
    return [(r["event_id"], f"Past negotiation ({r['date']}, {r['type']}): {r['note']}") for r in rows if r.get("note")]


def phrase_quote(label, items):
    """The sentence a phrase-list label matched, and its source."""
    for ref, text in items:
        m = re.search(supplier.SIGNALS[label], text or "", re.I)
        if m:
            start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
            end = text.find(".", m.end())
            return " ".join(text[start : end + 1 if end >= 0 else None].split())[:240], ref
    return None, None


def source_of(quote, items):
    n = reader._norm(quote)
    for ref, text in items:
        if n and n in reader._norm(text):
            return ref
    return None


def main():
    config.load_env()
    pkg = load_package()
    if pkg is None:
        raise SystemExit("no data package: run `buyer data` first")
    data = CaseData({"refs": []}, pkg)
    sids = [r["id"] for r in pkg.query("SELECT id FROM suppliers WHERE detail = 'full' ORDER BY id")]
    items, futures = {}, {}
    for sid in sids:
        its = supplier.supplier_text_items(data, sid) + history_texts(data, sid)
        items[sid] = its
        text = "\n\n".join(f"[{ref}]\n{t}" for ref, t in its if t)
        futures[sid] = reader.submit("everything our data holds about the supplier", text)
    try:  # the team's review survives a rebuild: a checked or corrected reading is kept as it is
        old = json.loads(OUT.read_text(encoding="utf-8")).get("suppliers") or {}
    except (OSError, ValueError):
        old = {}
    out, t0 = {}, time.time()
    for sid in sids:
        prev = old.get(sid) or {}
        if (prev.get("review") or {}).get("status") in ("checked", "corrected"):
            out[sid] = {**facts(data, sid), "reading": prev["reading"], "review": prev["review"]}
            continue
        its = items[sid]
        found = reader.result(futures[sid], 120) or {"signals": [], "asks": [], "evidence": {}, "summary": ""}
        phrase = supplier.signals_in("\n".join(t for _, t in its))
        signals = []
        for label in found["signals"]:
            q = found["evidence"].get(label, "")
            signals.append({"label": label, "quote": q, "source": source_of(q, its), "by": "reader agent"})
        for label in phrase:
            if label not in found["signals"]:
                q, src = phrase_quote(label, its)
                signals.append({"label": label, "quote": q, "source": src, "by": "phrase list"})
        out[sid] = {
            **facts(data, sid),
            "reading": {
                "signals": signals,
                "summary": found.get("summary") or "",
                "sources": [ref for ref, t in its if t and not ref.startswith("EV-")],
                "read_ok": bool(found.get("summary")),
            },
            "review": {"status": "auto", "note": ""},
        }
    doc = {
        "about": "Supplier dossier for the fully documented suppliers (detail = full). Built by "
        "scripts/build_dossier.py from the data package and the Sunday release.",
        "built_at": time.strftime("%Y-%m-%d %H:%M"),
        "model": reader.os.environ.get("LLM_MODEL"),
        "fields": FIELDS,
        "suppliers": out,
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    ok = sum(1 for e in out.values() if e["reading"]["read_ok"])
    print(f"{len(out)} suppliers, reader ok {ok}, {time.time() - t0:.0f}s, {reader.STATS} -> {OUT}")


if __name__ == "__main__":
    main()
