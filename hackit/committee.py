"""The purchasing committee: our departments sit opposite the supplier's departments.

The supplier is a team (sales, finance, logistics, quality, management). So are we:

  finance    how the supplier is paid and what money is worth to it (cash needs, overdue invoices)
  logistics  capacity, lead times, volume and commitment (idle or full machines, allocation)
  quality    delivery and defect record, complaints, quality commitments and chargebacks
  market     prices, cost drivers, indices, should-cost, rival quotes: the price argument
  director   hears the four, picks a package from the menu, the evidence and the tone

Each department reads the same situation through its own lens and pleads for its lever; the
director decides. Departments run in parallel, the director after them, all within the move's time
budget. The committee only ever picks: menu IDs (code-priced packages), evidence IDs (code-written
sentences) and words. A missing or late answer means code decides alone.
"""

from . import llm
from .dossier import NEEDS

DEPARTMENTS = {
    "finance": "payment terms, cash, what money is worth to the supplier, whether we pay it on time, "
    "cash discounts, its credit standing",
    "logistics": "capacity, lead times, volumes, contract length and commitment, allocation, delivery dates",
    "quality": "on-time delivery and defect record, complaints and 8D reports, ppm commitments and chargebacks",
    "market": "the price itself: cost drivers and market indices, should-cost, price history, rival offers "
    "and our alternative",
}
CONCESSION = ["hold", "small", "normal", "large"]
TONES = ["warm", "neutral", "firm"]

HOUSE = """We are Hollerau Pumpenwerke, a German pump maker, buying from an industrial supplier by e-mail.
Our score is the total cost of the deal over the contract (price, payment days, freight, lead time,
quality, coverage of our need, index clauses, and in strategic cases the supplier's health), not the
unit price alone. What we know about these suppliers:
- They concede only after a move from us, in shrinking steps, then send a final offer.
- Verified data in our FIRST e-mail lowers their first counter most; later rounds move little.
- Once a supplier says "agreed", that price is frozen: ask for other terms before it agrees.
- Threats, bluffs, deadlines and demands for their cost breakdown do not work. Polite questions
  ("what would help you move further?") and real trades do.
- Never reveal urgency, our budget, our target price or that we lack an alternative."""

DEPT_SYSTEM = (
    HOUSE
    + """

You are the {name} department of our purchasing committee. Your lens: {lens}.
Read the situation through your lens only and plead for the move your department would make.
The menu lists the only term packages we may send (code priced them: each costs us the same as M0).
The evidence lists the only data arguments we may cite. Pick by ID; never invent figures.
Answer in JSON."""
)

DEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "reading": {"type": "string", "description": "what your lens sees in the supplier's situation and last e-mail"},
        "supplier_needs": {"type": "array", "items": {"type": "string", "enum": sorted(NEEDS)}},
        "proposal": {"type": "string", "description": "the menu ID your department wants on the table, or 'none'"},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "evidence IDs, best first"},
        "concession": {"type": "string", "enum": CONCESSION},
        "why": {"type": "string"},
    },
    "required": ["reading", "supplier_needs", "proposal", "evidence", "concession", "why"],
    "additionalProperties": False,
}

DIRECTOR_SYSTEM = (
    HOUSE
    + """

You chair our purchasing committee. Your finance, logistics, quality and market departments have
each read the situation (their notes are below, some may be missing). Decide our next e-mail:
- main: the menu ID we propose as our main package (usually M0, or a trade the supplier needs).
- alternatives: up to two more menu IDs offered next to it, so the supplier's choice shows what it
  values. Offer trades that answer a need you can see; [] if none fits.
- evidence: up to three evidence IDs to cite, strongest first. Prefer evidence not used yet.
- concession: how far we move toward the supplier this round (hold / small / normal / large).
  Hold when it still moves a lot by itself; small with an approval ladder or a near-final supplier;
  large only when it is stuck and the gap is wide.
- tone and email_paragraph: one short paragraph (at most 90 words) answering the supplier's last
  e-mail as a person would: acknowledge its points, explain our position, offer what we can. NO
  digits, NO prices, NO dates, NO data IDs (code adds all figures and the proposal). No greeting,
  no sign-off: code adds them.
- When the supplier asks for something (faster payment, volume, commitment, time) and the menu has
  a package that gives it, offer that package as an alternative: a trade it asked for moves it most.
- question: one polite question that helps us learn what it values, or "".
- decision_note: one sentence for our minutes: why this move.
Answer in JSON."""
)

DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "main": {"type": "string"},
        "alternatives": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "concession": {"type": "string", "enum": CONCESSION},
        "tone": {"type": "string", "enum": TONES},
        "email_paragraph": {"type": "string"},
        "question": {"type": "string"},
        "decision_note": {"type": "string"},
    },
    "required": [
        "main",
        "alternatives",
        "evidence",
        "concession",
        "tone",
        "email_paragraph",
        "question",
        "decision_note",
    ],
    "additionalProperties": False,
}

STUDY_SYSTEM = (
    HOUSE
    + """

You are the {name} department of our purchasing committee. Your lens: {lens}.
Before the negotiation opens, study the supplier's dossier through your lens: what does it need,
what will it argue, what can we offer it that costs us little? Only use what the dossier shows.
Answer in JSON."""
)

STUDY_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": {"type": "string"}, "description": "up to 3 short findings"},
        "supplier_needs": {"type": "array", "items": {"type": "string", "enum": sorted(NEEDS)}},
        "its_likely_arguments": {"type": "array", "items": {"type": "string"}},
        "what_we_can_offer": {"type": "string"},
    },
    "required": ["findings", "supplier_needs", "its_likely_arguments", "what_we_can_offer"],
    "additionalProperties": False,
}


def study(situations, deadline_s):
    """Phase A: for every supplier ({key: situation}), the four departments read its dossier, all
    in parallel. {(key, department): findings or None}."""
    jobs = {
        (key, name): (STUDY_SYSTEM.format(name=name, lens=lens), situation, STUDY_SCHEMA)
        for key, situation in situations.items()
        for name, lens in DEPARTMENTS.items()
    }
    return llm.parallel(jobs, deadline_s)


def meet(situation, deadline_s, departments=True):
    """One round: departments in parallel, then the director. Returns (notes, decision) where
    decision is None when the director was late or wrong."""
    notes = {}
    director_s = deadline_s
    if departments:
        dept_s = deadline_s * 0.5
        jobs = {
            name: (DEPT_SYSTEM.format(name=name, lens=lens), situation, DEPT_SCHEMA)
            for name, lens in DEPARTMENTS.items()
        }
        notes = llm.parallel(jobs, dept_s)
        director_s = deadline_s - max([n["_seconds"] for n in notes.values() if n] or [dept_s])
    heard = (
        "\n".join(
            f"{name.upper()}: {n['reading']} Proposes {n['proposal']}, evidence {n['evidence']}, concession "
            f"{n['concession']}. Sees needs {n['supplier_needs']}. Why: {n['why']}"
            for name, n in notes.items()
            if n
        )
        or "No department notes this round: decide from the situation."
    )
    decision = llm.call(
        DIRECTOR_SYSTEM, f"{situation}\n\nYOUR DEPARTMENTS:\n{heard}", DIRECTOR_SCHEMA, max(4.0, director_s)
    )
    return notes, decision
