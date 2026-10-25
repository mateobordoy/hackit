"""E-mails. Every figure and every ID comes from code; the optional model paragraph may only add
words (no digits, no IDs, no leaks). Never mentions urgency, a budget, a missing alternative or
our target. Wording that measurably helped in the sandbox is the default: warm, acknowledging the
supplier's costs, and asking what it needs to move (D01: plain 50.00, warm 49.60, asking 49.40). [pc]"""

import re

from arena_kit.cost import closing_terms

from . import config

FIELDS = {
    "payment_days": lambda v: f"payment {v} days net",
    "contract_months": lambda v: f"{v} months" if v else "a single order",
    "volume_per_year": lambda v: f"{v} units a year",
    "lead_time_days": lambda v: f"lead time {v} days",
    "effective_date": lambda v: f"valid from {v}",
    "incoterm": lambda v: f"delivery {v}",
    "tooling_eur": lambda v: f"tooling EUR {v:.0f}" if v else "no tooling charge",
    "ppm_commitment": lambda v: f"{v} ppm",
    "chargeback_eur_per_complaint": lambda v: f"chargeback EUR {v:.0f} per complaint",
    "index_clause": lambda v: (
        f"price follows {v.get('series')} on {round((v.get('material_share') or 0) * 100)}% of the price, "
        f"deadband {v.get('deadband_pct') or 0}%"
        if v
        else "fixed price"
    ),
}
ASK = "What would you need from us to move further on the price? We are open to discussing any term that helps you."
ID_RE = re.compile(r"\b(?:idx|quote|offer|sc|qn|po|cost|fc|news|doc|ctr|sup|part|rfq):\S+", re.I)
LEAKS = re.compile(
    r"deadline|budget|walk(ing)? away|no (other )?alternative|other supplier|urgent|time pressure|target price"
    r"|our ceiling|maximum we|we must close|last chance|ultimatum|threat",
    re.I,
)


def describe(case, sheet):
    parts = [f"EUR {sheet['unit_price']:.2f} per unit"]
    for field in case["negotiable"]:
        if field != "unit_price" and field in sheet and field in FIELDS:
            parts.append(FIELDS[field](sheet[field]))
    fixed = case.get("fixed_terms") or {}
    if "volume_per_year" not in case["negotiable"]:
        volume = fixed.get("volume_per_year") or case["buyer"]["need_per_year"]
        months = sheet.get("contract_months") or fixed.get("contract_months")
        span = f" over {months} months" if months else ""
        parts.append(f"for our full demand of {volume} units a year{span}")
    return ", ".join(parts)


def statements(case, std, sheets, asks, first):
    """Code sentences with figures that answer its asks. A binding volume figure was "taken into
    account"; a description was not ("Which volume can you commit to?" came 152 times). [pc]"""
    out = []
    fixed = case.get("fixed_terms") or {}
    payment_fixed = "payment_days" not in case["negotiable"] or "payment_days" in fixed
    if "payment" in asks and payment_fixed:
        days = closing_terms(case, sheets[0], std).get("payment_days")
        out.append(f"Payment is fixed at {days} days under this agreement.")
    if "volume" in asks or first:
        sheet = sheets[0]
        volume = sheet.get("volume_per_year") or fixed.get("volume_per_year") or case["buyer"]["need_per_year"]
        months = sheet.get("contract_months") or fixed.get("contract_months")
        term = f"for the {months}-month term" if months else "for this agreement"
        full = " our full demand for this part," if volume >= case["buyer"]["need_per_year"] else ""
        out.append(f"We confirm a binding volume of {volume} units a year {term},{full} all placed with you.")
    return out


def email(case, sheets, claims, stmts=(), first=False, moved=False, warm=True, paragraph=None, choice_note=False):
    lines = ["Dear Sir or Madam,", ""]
    if first:
        lines.append("Thank you for your offer and for the time your team has put into it.")
    elif moved:
        lines.append("Thank you for your reply and for the step you have taken; we appreciate it.")
    else:
        lines.append("Thank you for your reply.")
    if warm:
        lines.append(
            "We value our cooperation and we understand the cost pressure on your side; we want an agreement "
            "that keeps our partnership healthy for both of us."
        )
    else:
        lines.append("We want an agreement that works for both sides.")
    # the writer agent's sentences come on top of the measured template sentences, never instead
    # of them: replacing them raised D01's counter (50.60 against 50.40 with the template)
    if paragraph:
        lines.append(paragraph)
    if stmts:
        lines += ["", *stmts]
    if claims:
        lines += ["", *[c["text"] for c in claims]]
    lines.append("")
    if len(sheets) == 1:
        lines.append(f"On this basis we propose: {describe(case, sheets[0])}.")
    else:
        lines.append("On this basis we propose one of these packages; please choose the one that suits you best:")
        lines += [f"- Option {chr(65 + i)}: {describe(case, s)}" for i, s in enumerate(sheets)]
    if first:
        lines += ["", ASK]
    lines += ["", "Kind regards,", "Hollerau Pumpenwerke, Purchasing"]
    return "\n".join(lines)


def accept_last_sheet_email(case, sheet):
    return "\n".join(
        [
            "Dear Sir or Madam,",
            "",
            "Thank you for your offer. We have reviewed it carefully and can confirm the following package:",
            f"{describe(case, sheet)}.",
            "",
            "Kind regards,",
            "Hollerau Pumpenwerke, Purchasing",
        ]
    )


def keepalive_message(events, case):
    return {
        "subject": reply_subject(events, case),
        "body": "Dear Sir or Madam,\n\nthank you for your offer, which we are reviewing internally. Could you "
        "confirm how long it remains valid?\n\nKind regards,\nHollerau Pumpenwerke, Purchasing",
        "term_sheets": [],
    }


def fallback_message(events, case):
    return {
        "subject": reply_subject(events, case),
        "body": "Dear Sir or Madam,\n\nthank you for explaining your position. We have noted your points and are "
        "reviewing the full commercial terms; a considered proposal follows.\n\nKind regards,\n"
        "Hollerau Pumpenwerke, Purchasing",
        "term_sheets": [],
    }


def reply_subject(events, case):
    subject = ""
    for m in reversed(events):
        if m.get("from") != "buyer" and m.get("subject"):
            subject = m["subject"]
            break
    subject = subject or f"Re: {case.get('title', 'your offer')}"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    return subject[:300]


def safe_paragraph(text):
    """The model's paragraph, or None when it carries a digit, a data ID or a leak."""
    if not text:
        return None
    text = " ".join(text.strip().strip('"').split())
    if not text or len(text) > config.LLM_MAX_CHARS:
        return None
    if re.search(r"\d", text) or ID_RE.search(text) or LEAKS.search(text):
        return None
    if re.search(r"dear |kind regards|best regards|sincerely", text, re.I):
        return None
    return text
