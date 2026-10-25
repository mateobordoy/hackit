"""The argument playbook from the synthesis lead's answer: the arguments the supplier is expected to
bring, our prepared answers, points worth raising and what it may want besides price.

The reply reader matches a real supplier e-mail to one of the expected arguments and the e-mail writer
receives our prepared answer. Words only; every price and term stays with code, and any line
containing a digit is dropped (the writer may not use figures)."""

import re

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
