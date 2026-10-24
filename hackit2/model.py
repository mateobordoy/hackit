"""Everything that talks to the language model.

The model never sets a number. It writes one paragraph of each e-mail and advises through small JSON
answers that the caller clamps to fixed lists. Code writes everything that binds or gets checked:
figures, data IDs, the proposal. A paragraph containing any digit, data ID or leak is discarded and
the template is used instead.

Calls run on a background pool with a hard wall-clock limit. A late call is abandoned, not
interrupted: it finishes in the background while the move goes out without it.

Transports (chosen by `connect`):
  anthropic  ANTHROPIC_API_KEY, LLM_MODEL, LLM_EFFORT - every call is priced and added to a spend file
             shared by all processes on the machine; past HACKIT_BUDGET_EUR no call is made.
  openai     LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, LLM_EFFORT - any OpenAI-compatible endpoint.
"""

import fcntl
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import settings, telemetry

log = logging.getLogger("hackit2")

_counts = Counter()
_counts_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=settings.MODEL_WORKERS, thread_name_prefix="model")

UNSAFE_TEXT = re.compile(
    r"\d|\b(?:idx|quote|offer|sc|qn|po|cost|fc|news|doc|ctr):|urgent|urgency|deadline|budget|no alternative|"
    r"walk away|other options|last chance|desperate|must close|time pressure|target price|"
    # giving away our price position (P11: "We agree that your casting and labour cost indices give no
    # case for a further price cut, and price is not our main focus here." The seller quoted it back.)
    r"no\s+case\s+for\s+(?:a\s+)?(?:further\s+)?price\s+(?:cut|reduction)|"
    r"price\s+is\s+not\s+our\s+(?:main\s+)?(?:focus|priority|concern)|"
    r"(?<!not\s)(?<!cannot\s)(?<!n't\s)(?<!unable\sto\s)\b(?:accept|agree)\s+(?:that\s+)?(?:the|your)\s+price|"
    r"price\s+(?:is|seems|looks)\s+(?:fair|reasonable|justified|acceptable)|"
    r"no\s+(?:room|scope)\s+for\s+(?:a\s+)?(?:further\s+)?(?:price\s+)?(?:cut|reduction)",
    re.I,
)

WRITER_SYSTEM = """You are a senior buyer at Hollerau Pumpenwerke, a German pump manufacturer, replying to a supplier.
Write ONLY the middle paragraph of our e-mail: two to four sentences of plain text, with no greeting,
no sign-off and no lists.

The paragraph should:
- reply to the supplier's last e-mail the way a thoughtful person would, addressing what it asked and
  the reasons it gave, using nothing but the facts listed under WHAT WE CAN SAY;
- briefly and sincerely acknowledge the supplier's situation where the notes describe one (for example
  a cash need, a full order book, rising costs, a long relationship);
- where it asks for something we cannot give, decline kindly and point to what we do offer;
- ask one short question only when QUESTION ALLOWED is yes.

Never write a number, price, percentage, date, quantity or data ID; code adds all figures. Never
mention urgency, deadlines, a budget, our alternatives or the lack of them, a target price, or walking
away. Never invent facts, promises or commitments. Never concede that the price is fair, justified or
not important to us, and never agree that there is no room to move on price. Stay polite, factual and
warm unless the tone says firm."""


# --- reliability counters ---------------------------------------------------------------------


def _count(name):
    with _counts_lock:
        _counts[name] += 1


def counters():
    """A thread-safe copy of the process-wide model counters."""
    with _counts_lock:
        return dict(_counts)


def counters_since(before):
    """Counter deltas since `before` (a `counters()` copy), with a failure rate per role."""
    after = counters()
    delta = {key: after.get(key, 0) - before.get(key, 0) for key in after.keys() | before.keys()}
    for role, failures in (
        ("paragraph", ("timeout", "error", "rejected")),
        ("helper", ("timeout", "error", "invalid")),
    ):
        attempts = delta.get(f"{role}.attempted", 0)
        failed = sum(delta.get(f"{role}.{failure}", 0) for failure in failures)
        delta[f"{role}.failure_rate"] = round(failed / attempts, 3) if attempts else None
    return delta


# --- spend cap (Anthropic transport) ----------------------------------------------------------

PRICE_PER_MTOK = {  # model -> (input, output) USD per million tokens
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
CACHE_READ_SHARE, CACHE_WRITE_SHARE = 0.1, 1.25
SPEND_WARN_SHARE = 0.8


class BudgetExceeded(RuntimeError):
    """Raised instead of calling the model once the spend budget is used up."""


def spend_file():
    return Path(os.environ.get("HACKIT_SPEND_FILE", "logs/spend.json"))


def budget_eur():
    return float(os.environ.get("HACKIT_BUDGET_EUR", "30"))


def spent_eur():
    try:
        return json.loads(spend_file().read_text())["eur"]
    except (OSError, ValueError, KeyError):
        return 0.0


def _record_spend(eur, tokens):
    """Add one call's cost to the shared total under an exclusive file lock; returns the new total."""
    path = spend_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        try:
            state = json.loads(handle.read() or "{}")
        except ValueError:
            state = {}
        state["eur"] = round(state.get("eur", 0.0) + eur, 6)
        state["calls"] = state.get("calls", 0) + 1
        for key, value in tokens.items():
            state[key] = state.get(key, 0) + value
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(state, indent=1))
        return state["eur"]


def call_cost_eur(model, usage):
    """EUR (counted 1:1 with USD, which stops early rather than late) for one response."""
    price_in, price_out = PRICE_PER_MTOK.get(model, PRICE_PER_MTOK["claude-opus-5-5"])
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * price_in
        + cache_read * price_in * CACHE_READ_SHARE
        + cache_write * price_in * CACHE_WRITE_SHARE
        + usage.output_tokens * price_out
    ) / 1e6


# --- transports -------------------------------------------------------------------------------


def _anthropic_transport():
    import anthropic

    model = os.environ.get("LLM_MODEL", "claude-opus-5-5")
    effort = os.environ.get("LLM_EFFORT", "low")
    client = anthropic.Anthropic(timeout=30.0, max_retries=1)
    warned = threading.Event()

    def complete(system, user):
        total = spent_eur()
        if total >= budget_eur():
            raise BudgetExceeded(f"model budget used up: {total:.2f} of {budget_eur():.0f} EUR")
        response = client.beta.messages.create(
            model=model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # a declined request is re-run on Anthropic's recommended model
        )
        usage = response.usage
        total = _record_spend(
            call_cost_eur(model, usage),
            {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        )
        if total >= budget_eur() * SPEND_WARN_SHARE and not warned.is_set():
            warned.set()
            log.warning("model spend %.2f EUR of %.0f EUR budget", total, budget_eur())
        if response.stop_reason == "refusal":
            raise RuntimeError("the model declined")
        return "".join(block.text for block in response.content if block.type == "text")

    return complete


def _openai_transport():
    model, key = os.environ.get("LLM_MODEL"), os.environ.get("LLM_API_KEY")
    if not model or not key:
        return None
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    effort = os.environ.get("LLM_EFFORT", "low")
    client = httpx.Client(timeout=settings.MODEL_TIMEOUT_S, headers={"Authorization": f"Bearer {key}"})

    def complete(system, user):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        response = client.post(
            f"{base}/chat/completions", json={"model": model, "messages": messages, "reasoning_effort": effort}
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    return complete


def connect():
    """complete(system, user) -> text for the configured provider, or None when none is configured."""
    provider = os.environ.get("LLM_PROVIDER") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai")
    if provider == "anthropic":
        return _anthropic_transport() if os.environ.get("ANTHROPIC_API_KEY") else None
    return _openai_transport()


# --- calling patterns -------------------------------------------------------------------------


def start(complete, system, user, role=None, supplier_id=None):
    """Start a model call in the background; a future for `collect_json`, or None without a model.
    `role` and `supplier_id` only label the call for the live view."""
    if not complete:
        return None
    channel = telemetry.current()
    if channel is None:
        return _pool.submit(complete, system, user)
    call = telemetry.next_call_id()
    channel.emit(
        "model_call",
        call=call,
        role=role,
        supplier=supplier_id,
        system=telemetry.excerpt(system, telemetry.SYSTEM_CHARS),
        user=telemetry.excerpt(user, telemetry.PROMPT_CHARS),
    )

    def run():
        started = time.monotonic()
        try:
            text = complete(system, user)
        except Exception as exc:
            channel.emit(
                "model_done",
                call=call,
                role=role,
                status="error",
                seconds=round(time.monotonic() - started, 2),
                error=telemetry.excerpt(repr(exc), 300),
            )
            raise
        channel.emit(
            "model_done",
            call=call,
            role=role,
            status="ok",
            seconds=round(time.monotonic() - started, 2),
            answer=telemetry.excerpt(text, telemetry.ANSWER_CHARS),
        )
        return text

    future = _pool.submit(run)
    future.telemetry_call = call
    return future


def _outcome(future, status):
    """Tell the live view what the caller made of a model answer (used, late, invalid, rejected)."""
    call = getattr(future, "telemetry_call", None)
    if call is not None:
        telemetry.emit("model_outcome", call=call, status=status)


def collect_json(future, timeout=None):
    """A helper's answer as a dict within `timeout` seconds, or None (no model, late, failed, not a
    JSON object). The caller clamps every value: nothing a model returns binds us unchecked."""
    timeout = settings.MODEL_TIMEOUT_S if timeout is None else timeout
    if future is None or timeout <= 0:
        _count("helper.unavailable")
        return None
    _count("helper.attempted")
    try:
        text = future.result(timeout=timeout) or ""
    except TimeoutError as exc:
        _count("helper.timeout")
        _outcome(future, "timeout")
        log.warning("helper timed out: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - a slow or broken model must not cost the move
        _count("helper.error")
        _outcome(future, "error")
        log.warning("helper failed: %r", exc)
        return None
    try:
        answer = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except (ValueError, TypeError, AttributeError):
        _count("helper.invalid")
        _outcome(future, "invalid")
        log.warning("helper returned invalid JSON")
        return None
    if not isinstance(answer, dict):
        _count("helper.invalid")
        _outcome(future, "invalid")
        return None
    _count("helper.success")
    _outcome(future, "used")
    return answer


def ask_json(complete, system, user, timeout=None, role=None, supplier_id=None):
    """Start a helper call and wait for its JSON answer (see `collect_json`)."""
    timeout = settings.MODEL_TIMEOUT_S if timeout is None else timeout
    if timeout < settings.MODEL_MIN_S:
        _count("helper.skipped_deadline")
        telemetry.emit("model_skipped", role=role, supplier=supplier_id, reason="no time left on the clocks")
        return None
    if complete is None:
        _count("helper.disabled")
        return None
    return collect_json(start(complete, system, user, role, supplier_id), timeout)


def write_paragraph(complete, prompt, timeout=None, supplier_id=None):
    """The model's middle paragraph for `prompt` within `timeout` seconds, or None (no model, no time,
    late, failed, too long or unsafe)."""
    timeout = settings.MODEL_TIMEOUT_S if timeout is None else timeout
    if complete is None:
        _count("paragraph.disabled")
        return None
    if timeout < settings.MODEL_MIN_S:
        _count("paragraph.skipped_deadline")
        telemetry.emit("model_skipped", role="writer", supplier=supplier_id, reason="no time left on the clocks")
        log.warning("paragraph skipped: %.1fs left on the clocks", timeout)
        return None
    started = time.monotonic()
    _count("paragraph.attempted")
    future = None
    try:
        future = start(complete, WRITER_SYSTEM, prompt, "writer", supplier_id)
        text = (future.result(timeout=min(timeout, settings.MODEL_TIMEOUT_S)) or "").strip()
    except TimeoutError as exc:
        _count("paragraph.timeout")
        _outcome(future, "timeout")
        log.warning("paragraph timed out after %.1fs: %s", time.monotonic() - started, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - a slow or broken model must not cost the move
        _count("paragraph.error")
        _outcome(future, "error")
        log.warning("paragraph failed after %.1fs: %s", time.monotonic() - started, exc)
        return None
    text = " ".join(text.split())
    if not text or len(text) > settings.PARAGRAPH_MAX_CHARS or UNSAFE_TEXT.search(text):
        _count("paragraph.rejected")
        _outcome(future, "rejected by the guard")
        log.warning("paragraph dropped: %r", text[:200])
        return None
    _count("paragraph.success")
    _outcome(future, "used")
    log.info("paragraph in %.1fs", time.monotonic() - started)
    return text
