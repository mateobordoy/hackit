"""Live telemetry for the watch UI (`buyer gui`, `liveview.py`).

While a case runs, every decision note, every e-mail in and out, every model call and the result are
appended as JSON lines to its live file (`logs/live/case_<P01>.jsonl`). Strictly passive: every write
is guarded, nothing here can change a decision, and with no channel bound (tests, tools, `--no-live`)
every hook is a no-op. The channel is per thread, so parallel cases write to their own files."""

import json
import os
import threading
import time

from . import config

_local = threading.local()
_lock = threading.Lock()
TEXT_CHARS = 6000


def enabled():
    return os.environ.get("BUYER_LIVE", "1") != "0"


def live_dir():
    return config.LOG_DIR / "live"


def bind(case_id):
    """Route this thread's telemetry to the case's live file; returns the path (None when off)."""
    if not enabled():
        _local.path = None
        return None
    try:
        live_dir().mkdir(parents=True, exist_ok=True)
        _local.path = live_dir() / f"case_{str(case_id).split(':')[-1]}.jsonl"
    except Exception:  # noqa: BLE001 - telemetry must never cost the case
        _local.path = None
    return _local.path


def unbind():
    _local.path = None


def clip(text, n=TEXT_CHARS):
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[:n] + " …"


def emit(event, **data):
    path = getattr(_local, "path", None)
    if path is None:
        return
    try:
        line = json.dumps({"at": round(time.time(), 3), "event": event, **data}, default=str, ensure_ascii=False)
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001 - telemetry must never cost the move
        pass


def case_start(case, session_id, threads):
    buyer = case.get("buyer") or {}
    emit(
        "case_start",
        case_id=case.get("case_id"),
        title=case.get("title"),
        type=case.get("type"),
        goal=buyer.get("category_goal"),
        set=case.get("set"),
        session=session_id,
        suppliers=list(threads),
        c_alt=_safe_c_alt(case),
        need_per_year=buyer.get("need_per_year"),
        horizon_years=buyer.get("horizon_years"),
        current_unit_price=buyer.get("current_unit_price"),
        negotiable=case.get("negotiable"),
        must_haves=case.get("must_haves") or buyer.get("must_haves"),
    )
    for sid, events in threads.items():
        for m in events:
            message_in(sid, m)


def _safe_c_alt(case):
    try:
        from .economics import c_alt

        value = c_alt(case)
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    except Exception:  # noqa: BLE001
        return None


def message_in(sid, m):
    emit(
        "message_in",
        supplier=sid,
        **{k: m.get(k) for k in ("from", "status", "term_sheets", "alternatives", "claim_results", "message_id")},
        body=clip(m.get("body")),
        attachments=[a.get("name") for a in m.get("attachments") or []],
    )


def message_out(sid, message):
    emit(
        "message_out",
        supplier=sid,
        **{k: message.get(k) for k in ("subject", "term_sheets", "refs", "action")},
        body=clip(message.get("body")),
        attachments=[a.get("name") for a in message.get("attachments") or []],
    )
