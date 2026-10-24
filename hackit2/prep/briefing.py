"""The preparation team, before the first e-mail. Four specialist models each study one slice of the
data about a supplier; a lead researcher turns their findings and earlier lessons into a strategy;
then a sparring supplier and a sparring buyer rehearse the arguments (`sparring.py`).

The strategy decides what goes on the table, never how fast we move: which terms this supplier
probably values more than they cost us, which arguments it will accept, and the tone. Code gathers
every figure a specialist sees and checks everything that comes back: a proposed trade is priced
with the cost function and only offered at a price that costs us no more than our main proposal.
Without a brief (no model, a timeout) the plain price engine negotiates.

A brief is a dict: trades, argument_order, tone, writer_notes, rationale, playbook, findings.

Controlled A/B runs (T21): with HACKIT_BRIEF_CACHE set to a folder, the briefs of a case are frozen in
`<folder>/<case>.json` the first time they are prepared and loaded from there afterwards, without any
model call, so both arms of a comparison negotiate from the same preparation."""

import json
import logging
import os
import time
from pathlib import Path

from .. import lessons, model, settings, telemetry
from . import sparring

log = logging.getLogger("hackit2")
BRIEF_CACHE_ENV = "HACKIT_BRIEF_CACHE"
ROUTINE_MAIL_WORDS = ("holiday", "iso ", "certificate", "contact person", "packaging", "trade fair")
ARGUMENT_TOPICS = ("should_cost", "order_history", "index", "notice", "rival", "forecast", "scorecard", "hedged")
NO_DATA_NOTE = "No data on this supplier: propose no trades, and take tone and notes from the case alone."
_briefs_by_case = {}  # a case resumed after a crash must not wait for the team again

# specialist -> (the data it studies, what it should report)
SPECIALISTS = {
    "economics": (
        "the case file, our should-cost estimate, the supplier's price history and the indices of its cost drivers",
        "how far its price sits above its cost, whether its cost drivers justify its price, "
        "and how much room there is on price",
    ),
    "persona": (
        "its company profile, its letters, our call and meeting notes, news about it and how we pay it",
        "who decides there and what moves them: cash, capacity, the relationship, approvals, frozen prices",
    ),
    "history": (
        "our past negotiations with it, our contract and its clauses, and how the part is sourced",
        "how far it usually moves from what it asks, what the contract obliges it to, and how dependent "
        "each side is on the other",
    ),
    "leverage": (
        "its delivery and quality record, our forecast, our order prices and rival quotes for the part",
        "which verified facts argue for a lower price or better terms, and which would argue its case",
    ),
}

SPECIALIST_SYSTEM = """You are the {name} analyst on the purchasing team of Hollerau Pumpenwerke, a German pump
manufacturer. Before we negotiate with a supplier you study one slice of our data: {scope}.
From the data below, report {goal}.
Use only the data given. If it says nothing useful, return no findings.
Return JSON only: {{"findings": [up to 4 short sentences, most important first],
"leverage": "ours" | "balanced" | "theirs"}}"""

HOW_SUPPLIERS_BEHAVE = """What we know about how these suppliers negotiate:
- Their first counter reflects the verified arguments they accept, not how low we open: the arguments
  we lead with in our first e-mail matter most.
- After that they concede in shrinking steps and stop after a few counters with a final offer, so a
  term they value has to be on the table early."""

LEAD_SYSTEM = (
    """You are the lead researcher on the purchasing team of Hollerau Pumpenwerke. Your analysts studied
one supplier ahead of a negotiation. Turn their findings into a negotiating strategy. Our price engine
sets prices and pace; you decide what we put on the table and how we argue.

"""
    + HOW_SUPPLIERS_BEHAVE
    + """

Return JSON only:
{"trades": up to 2 objects {"field": a negotiable field of the case other than unit_price,
  "value": one of that field's allowed options, exactly as the case lists it, "why": one short
  sentence}. A trade is a term this supplier probably values more than it costs us: faster payment
  for one short of cash, later delivery or a longer contract for a full order book, an index clause
  for one exposed to a volatile material, more volume for idle machines. Code prices every trade and
  offers it next to our main proposal at a price that costs us no more, so the supplier's choice
  reveals what it values. [] when the findings point to none.
 "argument_order": the kinds of argument this supplier will accept, strongest first, from: TOPICS.
 "tone": "warm" or "businesslike".
 "notes": two or three sentences for the colleague who writes our e-mails: who this supplier is,
  what it cares about and what we can honestly offer it. No figures, no dates.
 "reason": one sentence on why this strategy.}"""
).replace("TOPICS", ", ".join(ARGUMENT_TOPICS))


# --- data slices ------------------------------------------------------------------------------


def safe_query(pkg, sql, params=(), limit=8):
    try:
        return pkg.query(sql, params, limit)
    except Exception:  # noqa: BLE001 - a package without this table: the analyst sees less
        return []


def section(title, items):
    items = [item for item in items if item]
    if not items:
        return ""
    lines = (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False) for item in items)
    return f"{title}:\n" + "\n".join(lines)


def case_header(case):
    """The case as every team member sees it."""
    buyer = case["buyer"]
    keep = ("category_goal", "need_per_year", "horizon_years", "current_unit_price", "must_haves")
    return section(
        "CASE",
        [
            f"{case.get('title')} ({case.get('type')})",
            case.get("briefing"),
            {k: buyer.get(k) for k in keep},
            {"negotiable": case.get("negotiable"), "term_options": case.get("term_options")},
        ],
    )


# specialist -> [(title, SQL, parameter key, row limit)]
SLICES = {
    "economics": [
        (
            "OUR SHOULD-COST (EUR per unit, without margin)",
            "SELECT cost_base, supplier_id, unit_cost, material_eur, conversion_eur, overhead_eur, confidence_pct "
            "FROM should_cost WHERE part_id = ?",
            "part",
            8,
        ),
        (
            "ITS PRICES FOR THE PART",
            "SELECT valid_from, price, price_unit, basis FROM price_conditions "
            "WHERE part_id = ? AND supplier_id = ? ORDER BY valid_from DESC",
            "part_supplier",
            5,
        ),
        (
            "ITS LETTER",
            "SELECT date, subject, asked_pct, asked_price, effective_date, notice_days FROM documents WHERE id = ?",
            "letter",
            1,
        ),
    ],
    "persona": [
        (
            "VENDOR MASTER",
            "SELECT name, country, payment_days, skonto_days, skonto_pct, category, abc_class "
            "FROM suppliers WHERE id = ?",
            "supplier",
            1,
        ),
        (
            "COMPANY PROFILE (a lower credit index is better)",
            "SELECT legal_form, founded, owner_type, group_parent, staff, turnover_eur, equity_ratio_pct, "
            "credit_index, credit_index_12m_ago FROM supplier_intel WHERE supplier_id = ?",
            "supplier",
            1,
        ),
        (
            "HOW WE PAY ITS INVOICES",
            "SELECT round(avg(julianday(paid_date) - julianday(due_date)), 1) AS days_late, count(*) AS invoices "
            "FROM invoices WHERE supplier_id = ? AND paid_date IS NOT NULL HAVING count(*) > 0",
            "supplier",
            1,
        ),
        ("REMARKS ON ITS QUOTES", "SELECT remarks FROM quotes WHERE supplier_id = ? AND remarks != ''", "supplier", 4),
    ],
    "history": [
        (
            "PAST NEGOTIATIONS (asked and settled price change in %)",
            "SELECT date, type, asked_pct, settled_pct, note FROM negotiation_history "
            "WHERE supplier_id = ? ORDER BY date DESC",
            "supplier",
            10,
        ),
        (
            "CONTRACT",
            "SELECT type, valid_from, valid_to, payment_days, incoterm, notice_days, price_basis "
            "FROM contracts WHERE supplier_id = ?",
            "supplier",
            2,
        ),
        (
            "CLAUSES",
            "SELECT cl.type, cl.series, cl.material_share, cl.deadband_pct, cl.days, substr(cl.text, 1, 160) AS text "
            "FROM contract_clauses cl JOIN contracts c ON c.id = cl.contract_id WHERE c.supplier_id = ?",
            "supplier",
            8,
        ),
        (
            "WHO SUPPLIES THE PART",
            "SELECT supplier_id, status, quota_pct, tooling_owner FROM sourcing WHERE part_id = ?",
            "part",
            6,
        ),
    ],
    "leverage": [
        (
            "ITS SCORECARDS (on-time delivery, defects in ppm)",
            "SELECT quarter, otd_pct, ppm, qn_count, class FROM scorecards WHERE supplier_id = ? ORDER BY quarter DESC",
            "supplier",
            4,
        ),
        (
            "OUR FORECAST FOR THE PART",
            "SELECT snapshot_month, qty_next_12m FROM forecasts WHERE part_id = ? ORDER BY snapshot_month DESC",
            "part",
            3,
        ),
        (
            "OUR LAST ORDERS OF THE PART FROM IT",
            "SELECT order_date, qty, price, price_unit FROM po_lines "
            "WHERE part_id = ? AND supplier_id = ? ORDER BY order_date DESC",
            "part_supplier",
            4,
        ),
        (
            "QUOTES FOR THE PART (all suppliers)",
            "SELECT supplier_id, quote_date, unit_price, price_unit, lead_time_days, payment_days, awarded, remarks "
            "FROM quotes WHERE part_id = ? ORDER BY quote_date DESC",
            "part",
            8,
        ),
        (
            "WHAT WE SPEND WITH IT",
            "SELECT fiscal_year, round(sum(spend_eur)) AS our_spend_eur FROM spend_cube "
            "WHERE supplier_id = ? GROUP BY fiscal_year ORDER BY fiscal_year DESC",
            "supplier",
            2,
        ),
    ],
}


def driver_trends(pkg, part):
    """The part's cost drivers, each with its index change over the last twelve months."""
    drivers = []
    for row in safe_query(pkg, "SELECT series_id, weight FROM cost_drivers WHERE part_id = ?", (part,)):
        values = safe_query(
            pkg, "SELECT value FROM index_values WHERE series_id = ? ORDER BY month DESC", (row["series_id"],), 13
        )
        if len(values) == 13 and values[12]["value"]:
            row["change_12m_pct"] = round((values[0]["value"] / values[12]["value"] - 1) * 100, 1)
        drivers.append(row)
    return drivers


def records_about(pkg, supplier_id, prefix):
    """[(id, row)] for this supplier under an ID prefix, newest first, from the ID index (which the
    Sunday release reaches and the SQL tables do not)."""
    found = [(ref, pkg.lookup(ref) or {}) for ref in pkg.ids(prefix)]
    mine = (item for item in found if item[1].get("supplier") == supplier_id)
    return sorted(mine, key=lambda item: str(item[1].get("date")), reverse=True)


def guarded(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - a package without an ID index: the analyst sees less
        return []


def recent_letters(pkg, supplier_id):
    """Its latest letters and our notes about it, routine mail left out."""
    letters = []
    for ref, row in guarded(records_about, pkg, supplier_id, "doc:"):
        if any(word in (row.get("title") or "").lower() for word in ROUTINE_MAIL_WORDS) or len(letters) >= 6:
            continue
        try:
            letters.append(f"{row.get('date')} {row.get('type')}: {pkg.document(ref)[:700]}")
        except Exception:  # noqa: BLE001 - a row without a readable file
            letters.append(f"{row.get('date')} {row.get('type')}: {row.get('title')}")
    return letters


def recent_news(pkg, supplier_id, limit=5):
    """The latest news about it, with text."""
    items = []
    for ref, row in guarded(records_about, pkg, supplier_id, "news:")[:limit]:
        try:
            body = pkg.document(ref)[:500]
        except Exception:  # noqa: BLE001 - no readable text: the headline still counts
            body = ""
        items.append(f"{row.get('date')}: {row.get('headline')}. {body}".strip())
    return items


@telemetry.traced("prep.briefing.data_slice", result_view=lambda text: f"{len(text)} chars of data")
def data_slice(pkg, case, supplier_id, specialist):
    """The data one specialist studies, as text; empty when the package knows nothing relevant."""
    parts = case.get("parts") or []
    part = parts[0] if parts else None
    params = {
        "part": (part,),
        "supplier": (supplier_id,),
        "part_supplier": (part, supplier_id),
        "letter": (case.get("trigger_doc"),),
    }
    blocks = [section(title, safe_query(pkg, sql, params[key], limit)) for title, sql, key, limit in SLICES[specialist]]
    if specialist == "economics":
        blocks.append(
            section("COST DRIVERS OF THE PART (weight, index change over 12 months in %)", driver_trends(pkg, part))
        )
    if specialist == "persona":
        blocks.append(section("NEWS", recent_news(pkg, supplier_id)))
        blocks.append(section("LETTERS AND NOTES", recent_letters(pkg, supplier_id)))
    return "\n".join(filter(None, blocks))


# --- the strategy -----------------------------------------------------------------------------


def clamp_strategy(raw, case):
    """The lead's strategy with every value taken from the case or a fixed list; anything else dropped."""
    allowed = case.get("term_options") or {}
    trades = []
    for item in raw.get("trades") if isinstance(raw.get("trades"), list) else []:
        field = item.get("field") if isinstance(item, dict) else None
        if (
            field in case.get("negotiable", [])
            and field != "unit_price"
            and item.get("value") in allowed.get(field, [])
        ):
            why = " ".join(str(item.get("why") or "").split())[:160]
            trades.append({"field": field, "value": item["value"], "why": why})
    order = raw.get("argument_order")
    return {
        "trades": trades[: settings.PREP_TRADES_MAX],
        "argument_order": [k for k in order if k in ARGUMENT_TOPICS] if isinstance(order, list) else [],
        "tone": raw.get("tone") if raw.get("tone") in ("warm", "businesslike") else None,
        "writer_notes": " ".join(str(raw.get("notes") or "").split())[:600],
        "rationale": str(raw.get("reason") or "")[:300],
    }


@telemetry.traced("prep.briefing.prepare_briefs", result_view=lambda briefs: {"briefs_for": list(briefs)})
def prepare_briefs(case, pkg, complete, budget_s=None):
    """{supplier_id: brief} for a case: the specialists' findings, the lead's checked strategy and the
    sparring playbook. {} without a model; a supplier whose lead answered too late gets no brief.

    Three stages run within `budget_s`, each capped at `settings.PREP_STAGE_S`: specialists; lead and
    sparring supplier together; sparring buyer."""
    if complete is None:
        return {}
    if case["case_id"] in _briefs_by_case:
        return _briefs_by_case[case["case_id"]]
    frozen = load_frozen_briefs(case)
    if frozen is not None:
        _briefs_by_case[case["case_id"]] = frozen
        return frozen
    budget_s = settings.PREP_BUDGET_S if budget_s is None else budget_s
    started = time.monotonic()
    end = started + budget_s
    header = case_header(case)
    suppliers = case.get("suppliers") or []
    known = pkg is not None and any(pkg.lookup(ref) for ref in case.get("refs") or [])

    def stage_deadline():
        return min(end, time.monotonic() + settings.PREP_STAGE_S)

    # stage 1: specialists
    telemetry.emit("prep_stage", stage="specialists", status="start", suppliers=suppliers, data_known=known)
    pending = {}
    for supplier_id in suppliers:
        for name, (scope, goal) in SPECIALISTS.items():
            data = data_slice(pkg, case, supplier_id, name) if known else ""
            if data:
                system = SPECIALIST_SYSTEM.format(name=name, scope=scope, goal=goal)
                pending[supplier_id, name] = model.start(
                    complete, system, f"{header}\n\nSUPPLIER {supplier_id}\n{data}", f"specialist:{name}", supplier_id
                )
    deadline = stage_deadline()
    reports = {key: model.collect_json(future, deadline - time.monotonic()) for key, future in pending.items()}
    for (supplier_id, name), report in reports.items():
        telemetry.emit("prep_report", supplier=supplier_id, specialist=name, report=report)
    earlier = lessons.recall_lessons(case)
    telemetry.emit("prep_stage", stage="specialists", status="end", lessons=earlier)
    telemetry.emit("prep_stage", stage="lead_and_sparring", status="start")

    # stage 2: lead researcher and sparring supplier
    stage_two = {}
    for supplier_id in suppliers:
        findings = {name: (reports.get((supplier_id, name)) or {}) for name in SPECIALISTS}
        summary = "\n".join(
            f"{name.upper()} (leverage: {report.get('leverage', 'unknown')}): "
            + " ".join(str(x) for x in report.get("findings") or [])
            for name, report in findings.items()
            if report.get("findings")
        )
        user = "\n\n".join(
            filter(
                None,
                [
                    header,
                    f"SUPPLIER {supplier_id}, one of {len(suppliers)} in this case",
                    section("ANALYSTS", [summary or NO_DATA_NOTE]),
                    section("EARLIER NEGOTIATIONS OF THIS KIND (one run each: observations, not rules)", earlier),
                ],
            )
        )
        stage_two[supplier_id] = (
            findings,
            user,
            model.start(complete, LEAD_SYSTEM, user, "lead", supplier_id),
            model.start(complete, sparring.SUPPLIER_SYSTEM, user, "sparring_supplier", supplier_id),
        )

    # collect stage 2, then start stage 3: the sparring buyer answers the supplier's arguments
    deadline = stage_deadline()
    stage_three = {}
    for supplier_id, (findings, user, lead_future, supplier_future) in stage_two.items():
        strategy = model.collect_json(lead_future, deadline - time.monotonic())
        side = sparring.supplier_side(model.collect_json(supplier_future, deadline - time.monotonic()))
        buyer_future = None
        if side["arguments"]:
            buyer_future = model.start(
                complete,
                sparring.BUYER_SYSTEM,
                f"{user}\n\n{sparring.supplier_statement(side)}",
                "sparring_buyer",
                supplier_id,
            )
        stage_three[supplier_id] = (findings, strategy, side, buyer_future)
        telemetry.emit("prep_strategy", supplier=supplier_id, strategy=strategy, sparring_supplier=side)
    telemetry.emit("prep_stage", stage="lead_and_sparring", status="end")
    telemetry.emit("prep_stage", stage="sparring_buyer", status="start")

    deadline = stage_deadline()  # collect stage 3
    briefs = {}
    for supplier_id, (findings, strategy, side, buyer_future) in stage_three.items():
        if strategy is None:
            continue
        informed = any(findings.values())
        brief = clamp_strategy(strategy, case)
        if not informed:
            brief["trades"] = []  # a trade for a supplier we know nothing about is a guess
        brief["playbook"] = sparring.build_playbook(
            side, model.collect_json(buyer_future, deadline - time.monotonic()), informed
        )
        brief["findings"] = {name: r.get("findings") for name, r in findings.items() if r.get("findings")}
        briefs[supplier_id] = brief
        telemetry.emit("prep_brief", supplier=supplier_id, brief=brief)
    telemetry.emit("prep_stage", stage="sparring_buyer", status="end", seconds=round(time.monotonic() - started, 1))
    log.info(
        "%s preparation in %.1fs: %s",
        case["case_id"],
        time.monotonic() - started,
        json.dumps(briefs, ensure_ascii=False),
    )
    _briefs_by_case[case["case_id"]] = briefs
    freeze_briefs(case, briefs)
    return briefs


def frozen_briefs_path(case):
    """`<HACKIT_BRIEF_CACHE>/<case>.json` (case:P11 -> P11.json), or None when the variable is unset."""
    folder = os.environ.get(BRIEF_CACHE_ENV, "").strip()
    return Path(folder) / f"{case['case_id'].removeprefix('case:')}.json" if folder else None


def load_frozen_briefs(case):
    """The case's frozen briefs, or None (no cache folder, no file yet, or a file that cannot be read:
    then the team prepares as usual)."""
    path = frozen_briefs_path(case)
    if path is None or not path.is_file():
        return None
    try:
        briefs = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("%s: frozen briefs not readable (%s), preparing again", case["case_id"], exc)
        return None
    if not isinstance(briefs, dict):
        return None
    log.info("%s preparation loaded from cache %s", case["case_id"], path)
    for supplier_id, brief in briefs.items():
        telemetry.emit("prep_brief", supplier=supplier_id, brief=brief, frozen=True)
    return briefs


def freeze_briefs(case, briefs):
    """Write the briefs to the cache folder when one is set. An empty result (the team failed or ran
    out of time) is not frozen, so the next run prepares again. Never raises."""
    path = frozen_briefs_path(case)
    if path is None or not briefs:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(briefs, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("%s preparation frozen in %s", case["case_id"], path)
    except OSError as exc:
        log.warning("%s: briefs not frozen (%s)", case["case_id"], exc)
