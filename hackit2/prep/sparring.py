"""The sparring round before the real negotiation: a model playing the supplier and a model playing
our buyer argue the case. The supplier, in character and aware of what our analysts found, names the
arguments it will bring, the weak spots it hopes we miss and what it would trade price for. The
buyer answers each argument and picks the weak spots worth raising.

The result is a playbook: the reply reader matches a real supplier e-mail to one of the expected
arguments and the e-mail writer receives our prepared answer. Words only; every price and term stays
with code, and any line containing a digit is dropped (the writer may not use figures)."""

import re

SUPPLIER_SYSTEM = """You play the sales lead of a supplier in a private rehearsal of a negotiation with your
customer Hollerau Pumpenwerke. Below are the case and what the customer's analysts found out about
your company. Stay in character and be honest with yourself; nobody else will see this.
Return JSON only:
{"arguments": up to 4 arguments you will use to defend your price or turn down a request, one
  sentence each, strongest first,
 "weak_spots": up to 3 points in your position you hope the customer will not raise, one sentence each,
 "wants": up to 3 things other than price you would give a better price for, a short phrase each}
Write no figures, dates or names."""

BUYER_SYSTEM = """You are the senior buyer of Hollerau Pumpenwerke in a rehearsal of a negotiation. Below are
the case, what our analysts found, and what the supplier's sales lead said in the rehearsal: the
arguments it will use and the weak spots it hopes we miss.
Find our best answer to each of its arguments: reasoning that is fair, factual and hard to dismiss.
An answer may acknowledge the point, put it into proportion and refer to what the case and our
analysts' findings show we already bring, and to nothing else. An answer never promises or offers
anything new, sets no condition, and is never a threat, a deadline, a bluff or a request for a cost
breakdown (all tested, none works).
Return JSON only:
{"answers": one object per supplier argument, {"to": the argument's number, "answer": our answer in
  one or two sentences},
 "raise": up to 2 weak spots worth raising, each written as one polite, respectful sentence we could
  send, taken only from the analysts' findings and never about its finances; [] when the analysts
  found nothing}
Write no figures, dates or names: code adds the data."""

DIGIT = re.compile(r"\d")


def clean_lines(value, limit, max_chars=260):
    """A helper's list as clean sentences: strings only, no digits, capped in count and length."""
    lines = []
    for item in value if isinstance(value, list) else []:
        text = " ".join(str(item).split())[:max_chars] if isinstance(item, str) else ""
        if text and not DIGIT.search(text):
            lines.append(text)
    return lines[:limit]


def supplier_side(raw):
    """What the sparring supplier said, clamped."""
    raw = raw or {}
    return {
        "arguments": clean_lines(raw.get("arguments"), 4),
        "weak_spots": clean_lines(raw.get("weak_spots"), 3),
        "wants": clean_lines(raw.get("wants"), 3, 80),
    }


def supplier_statement(side):
    """The supplier's half of the rehearsal, as the sparring buyer reads it."""
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(side["arguments"], 1))
    weak = "\n".join(f"- {text}" for text in side["weak_spots"])
    return f"THE SUPPLIER'S ARGUMENTS:\n{numbered}\nWEAK SPOTS IT HOPES WE MISS:\n{weak or '- none named'}"


def build_playbook(side, raw, informed=True):
    """The rehearsal's outcome: each expected argument with our answer ("" when none was found), the
    weak spots to raise and what the supplier wants besides price. `informed`: our analysts had data
    on this supplier; without it a weak spot is a guess, and we never tell a supplier something our
    records do not show."""
    raw = raw or {}
    answers = {}
    for item in raw.get("answers") if isinstance(raw.get("answers"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("to"), int):
            text = clean_lines([item.get("answer")], 1, 400)
            if text:
                answers[item["to"]] = text[0]
    return {
        "arguments": side["arguments"],
        "answers": [answers.get(i, "") for i in range(1, len(side["arguments"]) + 1)],
        "raise": clean_lines(raw.get("raise"), 2) if informed else [],
        "wants": side["wants"],
    }
