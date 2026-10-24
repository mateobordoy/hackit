"""Volunteered seller department priorities, kept separate from verified commercial facts.

The presentation's slide 05 names Finance, Management, Engineering, Quality and Logistics.
We ask Sales once, alongside a real offer. Only explicit English needs in a department's own
sentence become hints; no hidden data, numerical valuation or permission is inferred from them.
"""

import re

QUESTION = (
    "If you are comfortable sharing, could you outline the priorities of your finance adviser "
    "and your management, engineering, quality and logistics colleagues for this agreement? "
    "Understanding what each team needs may help us find a better offer for both sides."
)

DEPARTMENTS = {
    "finance": r"\b(?:finance|financial|economist|economics|accounts|credit control)\b",
    "management": r"\b(?:management|managing director|pricing committee)\b",
    "engineering": r"\b(?:engineering|engineers?|technical team)\b",
    "quality": r"\bquality (?:team|manager|department|assurance|colleagues)\b"
    r"|(?:^|\n)\s*quality\s*:|\bquality (?:needs?|requires?|wants?|asks?|prefers?|insists?)\b",
    "logistics": r"\b(?:logistics|production planning|operations team|freight forwarder)\b",
}

# Direction comes from an explicit phrase, never merely from a department's name or a model guess.
NEEDS = {
    "earlier_payment": r"\b(?:faster|earlier|shorter|quicker|prompt) (?:\w+ ){0,2}(?:payment|settlement)"
    r"|\b(?:payment|settlement) (?:within|sooner|earlier)|\bpay(?:ing)? (?:sooner|earlier)",
    "firm_volume": r"\b(?:binding|firm|committed|higher) (?:\w+ ){0,2}(?:volume|demand|orders?)"
    r"|\bvolume commitment",
    "delivery_flexibility": r"\b(?:longer|extended) lead times?|\blater deliver(?:y|ies)"
    r"|\b(?:delivery|schedule) flexibility|\bflexible deliver(?:y|ies)",
    "longer_contract": r"\blonger (?:contract|agreement|term)|\blong.term commitment|\bplanning security",
    "forecast_proof": r"\b(?:forecast|volume) (?:data|evidence|proof)|\bproof of (?:the |our |your )?forecast",
    "technical_clarity": r"\b(?:drawings?|specifications?|technical clarification)\b",
    "quality_protection": r"\b(?:ppm|complaints?|chargebacks?|quality commitments?|quality standards?)\b",
    "approval": r"\b(?:sign.off|approv\w*|authori[sz]\w*)\b",
    "index_protection": r"\b(?:index clause|material risk|indexation)\b",
}

NEGATED = re.compile(r"\b(?:not|no|never|cannot|can't|don't|doesn't|do not|without|unnecessary)\b", re.I)
LABELS = {
    "earlier_payment": "earlier payment",
    "firm_volume": "a firm volume commitment",
    "delivery_flexibility": "more delivery flexibility",
    "longer_contract": "a longer commitment and planning security",
    "forecast_proof": "forecast evidence",
    "technical_clarity": "technical clarification",
    "quality_protection": "quality and complaint protections",
    "approval": "internal approval",
    "index_protection": "protection against material price changes",
}


def department_clauses(text):
    """A single named department can provide context within its own short paragraph or heading.

    Hosted replies use both 'Quality:\n...' and 'Faster payment ... priority for Finance'. A
    paragraph naming several departments remains ambiguous and needs attribution in each sentence.
    """
    for block in re.split(r"\n\s*\n", text):
        context = [name for name, pattern in DEPARTMENTS.items() if re.search(pattern, block, re.I)]
        for clause in re.split(r"[.!?;\n]+", block):
            clause = " ".join(clause.split())
            if not clause or len(clause) > 500:
                continue
            departments = [name for name, pattern in DEPARTMENTS.items() if re.search(pattern, clause, re.I)]
            if not departments and len(context) == 1 and len(block) <= 1200:
                departments = context
            if len(departments) == 1:
                yield departments[0], clause, block


def update(previous, body):
    """Replace a department's hints when it states a new need; explicit retractions remove that need.

    Negated or ambiguous sentences do not create a hint. Source excerpts are for the audit log only;
    the writer receives fixed labels, never arbitrary instructions quoted from the supplier.
    """
    text = (body or "")[:8000]
    found = {}
    replaced, retracted = set(), set()
    for department, clause, context in department_clauses(text):
        for need, pattern in NEEDS.items():
            if re.search(pattern, clause, re.I):
                key = (department, need)
                if NEGATED.search(clause):
                    retracted.add(key)
                    found.pop(key, None)
                    continue
                replaced.add(department)
                retracted.discard(key)
                item = {"department": department, "need": need, "source": clause, "context": context}
                found[key] = item
    kept = [
        item
        for item in previous
        if item["department"] not in replaced and (item["department"], item["need"]) not in retracted
    ]
    return (kept + list(found.values()))[:12]


def requests(priorities):
    """Existing request types we can answer with code-written statements and attachments."""
    kinds = {"earlier_payment": "payment", "firm_volume": "volume", "forecast_proof": "proof"}
    return {kinds[item["need"]] for item in priorities if item["need"] in kinds}


def wanted_trades(priorities):
    """Directions to examine in the existing cost-checked package builder, never actual term values."""
    directions = {
        "earlier_payment": ("payment_days", "lower"),
        "firm_volume": ("volume_per_year", "higher"),
        "delivery_flexibility": ("lead_time_days", "higher"),
        "longer_contract": ("contract_months", "higher"),
    }
    return {directions[item["need"]] for item in priorities if item["need"] in directions}


def writer_facts(priorities):
    return [
        f"their {item['department']} team mentioned {LABELS[item['need']]}; acknowledge this only where our "
        "proposal or supplied evidence addresses it; make no extra promise or unapproved concession"
        for item in priorities
    ]
