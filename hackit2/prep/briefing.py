"""Shared preparation helpers and the entry point of the research team (`alpha.prepare`: eight
specialists, the Scout researcher and a synthesis lead).

The strategy decides what goes on the table, never how fast we move: which terms this supplier
probably values more than they cost us, which arguments it will accept, and the tone. Code gathers
every figure a specialist sees and checks everything that comes back: a proposed trade is priced
with the cost function and only offered at a price that costs us no more than our main proposal.
Without a brief (no model, a timeout) the plain price engine negotiates.

A brief is a dict: trades, argument_order, tone, writer_notes, rationale, playbook, findings.
With HACKIT_BRIEF_CACHE set to a folder, a case's briefs are frozen there the first time and loaded
afterwards without any model call (a restarted final, a controlled comparison)."""

import json

from .. import settings, telemetry

BRIEF_CACHE_ENV = "HACKIT_BRIEF_CACHE"
ARGUMENT_TOPICS = ("should_cost", "order_history", "index", "notice", "rival", "forecast", "scorecard", "hedged")

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


# --- shared helpers ---------------------------------------------------------------------------


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
    """{supplier_id: brief} from the research team (`alpha.prepare`); {} without a model."""
    from .alpha import prepare  # alpha imports this module's helpers

    return prepare(case, pkg, complete, budget_s)
