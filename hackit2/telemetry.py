"""Live telemetry for the watch UI (`tools/live_view.py`).

While a case runs, everything worth seeing is appended as JSON lines to its live file
(`logs/live/case_<id>.jsonl`): the engine's decisions, every model call with its role, prompt excerpt,
answer and latency, the preparation stages, every e-mail in and out, and which function called which
with what data (`traced`).

Telemetry is strictly passive: every write is guarded, nothing it does can change a decision, and with
no channel bound (tests, tools) every hook is a no-op."""

import contextvars
import functools
import itertools
import json
import threading
import time

_channel = contextvars.ContextVar("hackit2_live_channel", default=None)
_write_lock = threading.Lock()
_call_ids = itertools.count(1)
_stack = threading.local()

PROMPT_CHARS = 6000
SYSTEM_CHARS = 1200
ANSWER_CHARS = 4000
VALUE_CHARS = 240


def append(path, entry):
    """Append one JSON line to `path`; never raises."""
    try:
        line = json.dumps(entry, default=str, ensure_ascii=False)
        with _write_lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:  # noqa: BLE001 - telemetry must never cost the move
        pass


class Channel:
    """The live file of one case."""

    def __init__(self, path):
        self.path = path

    def emit(self, event, **data):
        append(
            self.path,
            {"at": round(time.time(), 3), "event": event, "thread": threading.current_thread().name, **data},
        )


def bind(path):
    """Route telemetry of the current thread to `path`; returns a token for `unbind`."""
    return _channel.set(Channel(path) if path else None)


def unbind(token):
    try:
        _channel.reset(token)
    except (ValueError, RuntimeError):
        _channel.set(None)


def current():
    return _channel.get()


def emit(event, **data):
    channel = _channel.get()
    if channel is not None:
        channel.emit(event, **data)


def next_call_id():
    return next(_call_ids)


def excerpt(text, limit):
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[:limit] + f" …[+{len(text) - limit} chars]"


def summarize(value, depth=0):
    """A compact, JSON-friendly view of a value for the trace feed."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return excerpt(value, VALUE_CHARS)
    if isinstance(value, dict):
        if depth >= 2 or len(value) > 14:
            return f"{{{len(value)} keys}}"
        return {str(k): summarize(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if depth >= 2 or len(items) > 6:
            return f"[{len(items)} items]"
        return [summarize(v, depth + 1) for v in items]
    return excerpt(type(value).__name__, 60)


def _argument_view(args, kwargs):
    """Scalars and small values only: the case file, data package and big objects are left out."""
    view = {}
    for index, value in enumerate(args):
        if isinstance(value, (str, int, float, bool)) or value is None:
            view[f"arg{index}"] = summarize(value)
        elif isinstance(value, dict) and "unit_price" in value:
            view[f"arg{index}"] = {"unit_price": value.get("unit_price")}
        elif hasattr(value, "supplier_id"):
            view["supplier"] = value.supplier_id
    for key, value in kwargs.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            view[key] = summarize(value)
    return view


def traced(label, result_view=None):
    """Decorator: with a channel bound, emit `trace` start/end events (caller, arguments, result,
    duration) around the function; otherwise call it directly. Exceptions pass through unchanged."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            channel = _channel.get()
            if channel is None:
                return fn(*args, **kwargs)
            stack = getattr(_stack, "labels", None)
            if stack is None:
                stack = _stack.labels = []
            call = next_call_id()
            parent = stack[-1] if stack else None
            channel.emit("trace", phase="start", fn=label, parent=parent, call=call, args=_argument_view(args, kwargs))
            stack.append(label)
            started = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                stack.pop()
                channel.emit(
                    "trace",
                    phase="error",
                    fn=label,
                    parent=parent,
                    call=call,
                    ms=round((time.perf_counter() - started) * 1000, 2),
                    error=excerpt(repr(exc), 300),
                )
                raise
            stack.pop()
            try:
                shown = result_view(result) if result_view else summarize(result)
            except Exception:  # noqa: BLE001
                shown = None
            channel.emit(
                "trace",
                phase="end",
                fn=label,
                parent=parent,
                call=call,
                ms=round((time.perf_counter() - started) * 1000, 2),
                result=shown,
            )
            return result

        return inner

    return wrap


# --- result views for `traced` -------------------------------------------------------------------


def message_view(move):
    """A move ({"message": ...} or None) as its sheet prices and number of refs."""
    if move is None:
        return "no message: thread finished"
    message = move.get("message") or {}
    sheets = [(sheet.get("terms") or {}).get("unit_price") for sheet in message.get("term_sheets") or []]
    return {"sheets": sheets, "refs": len(message.get("refs") or [])}


def prices_view(sheets):
    return [sheet.get("unit_price") for sheet in sheets or []]


def topics_view(arguments):
    return [argument.get("topic") for argument in arguments or []]


def thread_view(thread):
    return {
        "phase": thread.phase,
        "opening": thread.opening_price,
        "quote_at_plan": thread.quote_at_plan,
        "sweet_spot": thread.sweet_spot,
        "patience": thread.patience,
    }


def decision_view(decision):
    return {"supplier": decision.get("supplier_id"), "action": (decision.get("message") or {}).get("action")}


def plan_view(planned):
    return None if planned is None else {"unit_price": planned[0].get("unit_price"), "C": round(planned[1])}
