"""Side-by-side messages of the hypothesis A/B arms: how playing a hypothesis changed what we sent and
what the supplier answered.

Reads the arms' session ids from `results.json` (written by `hypothesis_ab.py`), the full conversation
of each session from the arena (`GET /v1/sessions/{id}`: both sides' e-mails, sheets and claim checks)
and, from the ON arm's run journal, which hypothesis was played before which of our messages.
Writes `messages.md` next to `results.json`. Read-only: no session is opened.

    uv run python scripts/hypothesis_ab_messages.py logs/hypothesis-ab-practice
"""

import json
import sys
from pathlib import Path

from arena_kit import ArenaClient
from arena_kit.cli import load_env

EXCERPT = 420


def terms_text(terms, keys=("payment_days", "lead_time_days", "contract_months", "incoterm", "volume_per_year")):
    extra = ", ".join(f"{k.replace('_', ' ')} {terms[k]}" for k in keys if terms.get(k) is not None)
    clause = terms.get("index_clause")
    if isinstance(clause, dict):
        extra += (
            f"{', ' if extra else ''}**index clause {clause.get('series')} deadband {clause.get('deadband_pct')} %**"
        )
    return f"EUR {terms.get('unit_price')}" + (f" ({extra})" if extra else "")


def turns(state):
    """[(our move or None, its reply or None)] in order, from the session trace."""
    out, ours = [], None
    for step in (state.get("result") or {}).get("trace") or []:
        move = step.get("move") or {}
        if step.get("actor") == "buyer":
            if ours is not None:
                out.append((ours, None))
            ours = move
        else:
            out.append((ours, move))
            ours = None
    if ours is not None:
        out.append((ours, None))
    return out


def ours_text(move):
    if move is None:
        return "-"
    kind = move.get("type")
    if kind != "message":
        return f"**{kind}** {move.get('offer_id') or ''}"
    sheets = [s.get("terms") or {} for s in move.get("term_sheets") or []]
    lines = [f"sheet {i + 1}: {terms_text(t)}" for i, t in enumerate(sheets)]
    body = " ".join((move.get("body") or "").split())
    return "<br>".join(lines) + f"<br>_{body[:EXCERPT]}{'...' if len(body) > EXCERPT else ''}_"


def theirs_text(move):
    if move is None:
        return "-"
    sheets = [move.get("terms") or {}] + [a.get("terms") or {} for a in move.get("alternatives") or []]
    claims = ", ".join(str(c.get("result")) for c in move.get("claim_results") or [])
    text = " ".join((move.get("text") or "").split())
    head = f"**{move.get('status')}** " + " / ".join(terms_text(t) for t in sheets if t)
    return (
        head
        + (f"<br>claims: {claims}" if claims else "")
        + f"<br>_{text[:EXCERPT]}{'...' if len(text) > EXCERPT else ''}_"
    )


def played_by_message(journal):
    """For the ON arm: the hypothesis played before each of our sheet-carrying messages, in order."""
    out, pending = [], None
    for event in journal:
        if event.get("event") == "hypothesis_played":
            pending = f"{event['id']}: {event['move']} (p {event['p']})"
        elif event.get("event") == "send":
            out.append(pending)
            pending = None
    return out


def main(folder):
    load_env()
    folder = Path(folder)
    rows = json.loads((folder / "results.json").read_text())
    client = ArenaClient()
    lines = ["# Hypothesis A/B: messages side by side", ""]
    for case in dict.fromkeys(r["case"] for r in rows):
        off = next(r for r in rows if r["case"] == case and r["arm"] == "off")
        on = next(r for r in rows if r["case"] == case and r["arm"] == "on")
        journal_file = folder / "runs-on" / f"{case.split(':')[-1]}_{on['session']}.json"
        journal = json.loads(journal_file.read_text())["journal"] if journal_file.is_file() else []
        played = played_by_message(journal)
        updates = [e for e in journal if e.get("event") == "hypothesis_update"]
        off_turns, on_turns = turns(client.session(off["session"])), turns(client.session(on["session"]))
        lines += [
            f"## {case}: off {off['score']:.3f} ({off['session']}) vs on {on['score']:.3f} ({on['session']}),"
            f" C {off['c_deal']} vs {on['c_deal']}",
            "",
            "| Round | Hypothesis played (on) | Off: we sent | Off: supplier | On: we sent | On: supplier |",
            "|---|---|---|---|---|---|",
        ]
        sent = 0
        for i in range(max(len(off_turns), len(on_turns))):
            o_ours, o_theirs = off_turns[i] if i < len(off_turns) else (None, None)
            n_ours, n_theirs = on_turns[i] if i < len(on_turns) else (None, None)
            hyp = "-"
            if n_ours is not None and n_ours.get("type") == "message":
                hyp = (played[sent] if sent < len(played) else None) or "-"
                sent += 1
            cells = [str(i), hyp, ours_text(o_ours), theirs_text(o_theirs), ours_text(n_ours), theirs_text(n_theirs)]
            lines.append("| " + " | ".join(cells) + " |")
        if updates:
            lines += [
                "",
                "Updates (on): " + "; ".join(f"{e['id']} {e['outcome']} -> {e['p']} ({e['reason']})" for e in updates),
            ]
        lines.append("")
    out = folder / "messages.md"
    out.write_text("\n".join(lines))
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/hypothesis-ab-practice")
