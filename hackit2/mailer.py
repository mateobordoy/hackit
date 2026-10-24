"""E-mails built from templates. Every figure and data ID comes from code; the wording stays polite
and factual and never mentions urgency, a budget, the absence of an alternative or our target."""

from arena_kit.cost import closing_terms
from arena_kit.play import latest

from . import adviser_priorities, telemetry, transcript

SIGNATURE = ["Kind regards,", "Hollerau Pumpenwerke, Purchasing"]

TERM_PHRASES = {
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


def describe_sheet(case, sheet):
    """One sheet in words: price first, then every negotiable term, then the volume it covers."""
    phrases = [f"EUR {sheet['unit_price']:.2f} per unit"]
    for field in case["negotiable"]:
        if field != "unit_price" and field in sheet and field in TERM_PHRASES:
            phrases.append(TERM_PHRASES[field](sheet[field]))
    fixed = case.get("fixed_terms") or {}
    volume = sheet.get("volume_per_year") or fixed.get("volume_per_year") or case["buyer"]["need_per_year"]
    months = sheet.get("contract_months") or fixed.get("contract_months")
    span = f" over {months} months" if months else ""
    if "volume_per_year" not in case["negotiable"]:
        phrases.append(f"for our full demand of {volume} units a year{span}")
    return ", ".join(phrases)


# Sandbox, identical sheets with only the wording varied: plain, firm, warm, and asking what the
# supplier needs, each ending lower than the last. Asking invites the trade it wants.
OPEN_QUESTION = (
    "What would you need from us to move further on price? We are open to discussing any term that would help you."
)


@telemetry.traced("mailer.compose_email")
def compose_email(
    case, sheets, arguments, statements=(), first=False, moved=False, warm=True, paragraph=None, ask_advisers=False
):
    """The e-mail: our words (the model's paragraph, otherwise a template), then code sentences that
    answer its requests (figures, no IDs), then the data arguments, then the proposal exactly as code
    computed it."""
    lines = ["Dear Sir or Madam,", ""]
    if first:
        lines.append("Thank you for your offer and for the time your team has invested in it.")
    elif moved:
        lines.append("Thank you for your reply and for the step you have taken, which we appreciate.")
    else:
        lines.append("Thank you for your reply.")
    if paragraph:
        lines.append(paragraph)
    elif warm:
        lines.append(
            "We value our cooperation and understand the cost pressure on your side; we are looking for an "
            "agreement that keeps our partnership healthy for both of us."
        )
    else:
        lines.append("We are looking for an agreement that works for both sides.")
    if statements:
        lines.append("")
        lines += list(statements)
    if arguments:
        lines.append("")
        lines += [a["sentence"] for a in arguments]
    lines.append("")
    if len(sheets) == 1:
        lines.append(f"On this basis we propose: {describe_sheet(case, sheets[0])}.")
    else:
        lines.append("On this basis we propose one of the following packages:")
        lines += [f"- Option {chr(65 + i)}: {describe_sheet(case, sheet)}" for i, sheet in enumerate(sheets)]
    if ask_advisers:
        lines += ["", adviser_priorities.QUESTION]
    elif first and not paragraph:
        lines += ["", OPEN_QUESTION]
    lines += ["", *SIGNATURE]
    return "\n".join(lines)


TRAIT_NOTES = {
    "cash_need": "it is short of cash; when it gets paid matters a great deal to it",
    "capacity_full": "it is fully booked; delivery time and a firm commitment matter to it",
    "spare_capacity": "it has free capacity and wants work for its machines",
    "price_frozen": "its management has frozen price cuts; other terms can move",
    "approval_2pct": "larger price steps need the approval of its pricing committee",
    "deprioritising_us": "it is focusing on customers with larger volumes",
    "hedged": "it bought its material forward at fixed prices",
    "open_book": "its owner once offered to take us through its calculation",
    "stale_costing": "its costing has not kept up with actual material prices",
    "partnership_2022": "it kept supplying at the old price for a quarter during the last crisis",
    "reference_customer": "it values having our company as a reference customer",
    "complains_late_pay": "it has complained that our invoices were paid late",
}
OWNERSHIP_NOTES = {
    "family": "a family-owned company where the relationship matters",
    "group": "part of a larger group with formal approvals",
    "founder": "a small founder-run shop",
    "private": "a privately held company",
}


@telemetry.traced("mailer.paragraph_prompt", result_view=lambda text: f"{len(text)} chars")
def paragraph_prompt(case, profile, last_body, facts, tone, question_allowed, insight=(), history=None):
    """What the model needs for the middle paragraph: who the supplier is, what it wrote, what we may
    say. `insight`: the preparation team's and the reply reader's notes on this supplier."""
    who = OWNERSHIP_NOTES.get(profile.get("ownership"), "a supplier")
    if profile.get("country") and profile.get("country") != "DE":
        who += f" based in {profile['country']}"
    traits = [TRAIT_NOTES[t] for t in profile.get("traits", []) if t in TRAIT_NOTES]
    lines = [
        f"CASE: {case.get('title')} (we are the buyer; goal: {case['buyer'].get('category_goal')})",
        f"SUPPLIER: {who}." + (f" From our notes: {'; '.join(traits)}." if traits else ""),
        *[f"OUR TEAM'S READING OF THEM (background, never quote it): {note}" for note in insight if note],
        f"ITS LAST E-MAIL:\n{(last_body or '')[:1800]}",
        "WHAT WE CAN SAY:",
        *[f"- {fact}" for fact in facts],
        f"TONE: {tone}",
        f"QUESTION ALLOWED: {'yes' if question_allowed else 'no'}",
    ]
    if history is not None:
        lines.append(transcript.as_prompt(history))
    return "\n".join(lines)


def keepalive_body():
    return "\n".join(
        [
            "Dear Sir or Madam,",
            "",
            "thank you for your offer, which we are currently reviewing internally. Could you confirm how long it "
            "remains valid?",
            "",
            *SIGNATURE,
        ]
    )


@telemetry.traced("mailer.speakable_facts", result_view=lambda facts: f"{len(facts)} facts")
def speakable_facts(case, thread, arguments, moved, statements, attachments, clause_traded):
    """Plain statements the model may use, true by construction. The supplier's own requests come first
    so the paragraph answers them (a supplier that kept asking for faster payment without an answer
    stopped moving altogether)."""
    facts = []
    fixed = case.get("fixed_terms") or {}
    payment_fixed = "payment_days" not in case["negotiable"] or fixed.get("payment_days")
    if "payment" in thread.requests:
        if payment_fixed:
            facts.append(
                "they asked for faster payment: payment terms are fixed in this agreement and cannot change; say so "
                "plainly and kindly, and point to what we do give (our full volume, the contract term)"
            )
        else:
            facts.append("they asked for faster payment: our proposal below sets the payment term we can offer")
    if "volume" in thread.requests or any("binding volume" in text for text in statements):
        facts.append(
            "they asked which volume we commit to: the binding volume figure follows right after this paragraph"
        )
    if attachments:
        facts.append("they asked for proof of our forecast: the forecast is attached to this e-mail as a file, say so")
    if "volume_per_year" not in case["negotiable"] or fixed.get("volume_per_year"):
        facts.append(
            "our order volume is fixed: it is our full demand for this part over the term, all placed with them"
        )
    if payment_fixed and "payment" not in thread.requests:
        facts.append("payment terms are fixed for this agreement")
    if "contract_months" in case["negotiable"]:
        facts.append("we offer a long contract term, which gives them planning security")
    if thread.plan.get("index_clause") and clause_traded:
        facts.append(
            "instead of a fixed price we propose an index clause with a deadband on their main cost driver, so both "
            "sides share the material risk; in return the price basis reflects that"
        )
    if any(a["topic"] == "forecast" for a in arguments) or any(a["topic"] == "forecast" for a in thread.evidence):
        facts.append("our demand forecast for this part is rising")
    if len(thread.our_prices) > 1 and thread.our_prices[-1] > thread.our_prices[-2]:
        facts.append("our proposal moves toward them again")
    if moved:
        facts.append("they moved in their last reply and we appreciate it")
    if arguments:
        facts.append("the e-mail cites data right after this paragraph (do not repeat it)")
    facts.append("the proposal with all terms follows after the data (do not repeat it)")
    return facts


@telemetry.traced("mailer.binding_statements")
def binding_statements(case, thread, sheets):
    """Code sentences with figures that answer its requests: a fixed payment term when it asks for
    faster payment, and the binding volume in our first message and whenever it asks (a stated figure
    was taken into account by the suppliers, a description was not)."""
    statements = []
    fixed = case.get("fixed_terms") or {}
    if "payment" in thread.requests and ("payment_days" not in case["negotiable"] or "payment_days" in fixed):
        days = closing_terms(case, sheets[0], thread.standard).get("payment_days")
        statements.append(f"Payment is fixed at {days} days under this agreement and cannot be shortened.")
    if not ("volume" in thread.requests or not thread.our_prices):
        return statements
    sheet = sheets[0]
    need = case["buyer"]["need_per_year"]
    volume = sheet.get("volume_per_year") or fixed.get("volume_per_year") or need
    months = sheet.get("contract_months") or fixed.get("contract_months")
    term = f"for the {months}-month term" if months else "for this agreement"
    full = " our full demand for this part," if volume >= need else ""
    statements.append(f"We confirm a binding volume of {volume} units a year {term},{full} all placed with you.")
    return statements


def reply_subject(events, case):
    subject = latest(events).get("subject") or f"Re: {case['title']}"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    return subject[:300]


def keepalive_message(events):
    subject = latest(events).get("subject") or "Re: your offer"
    return {"subject": subject[:300], "body": keepalive_body(), "term_sheets": []}


def holding_email(statements=()):
    """A deterministic reply for engine or model failures; it never repeats a term sheet."""
    lines = [
        "Dear Sir or Madam,",
        "",
        "Thank you for explaining your position. We have noted your points and are reviewing the full commercial",
        "terms.",
        *statements,
        "We will follow up with a considered proposal.",
        "",
        *SIGNATURE,
    ]
    return "\n".join(lines)
