"""Claude calls for the committee: JSON out (structured outputs), hard deadlines, never raises.

Every call returns the parsed JSON or None (late, refused, broken); callers fall back to code.
Several calls run in parallel, so four departments cost the time of one.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, wait

import anthropic

log = logging.getLogger("hackit")
POOL = ThreadPoolExecutor(max_workers=24)
_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=0)
    return _client


def model():
    return os.environ.get("HACKIT_MODEL", "claude-opus-5-5")


def effort():
    return os.environ.get("HACKIT_EFFORT", "low")


def call(system, user, schema, timeout, max_tokens=1500):
    """One structured call; the parsed object or None."""
    t0 = time.monotonic()
    try:
        r = (
            client()
            .with_options(timeout=timeout)
            .messages.create(
                model=model(),
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": effort(), "format": {"type": "json_schema", "schema": schema}},
            )
        )
    except anthropic.APIError as e:
        log.warning("model call failed after %.1fs: %s", time.monotonic() - t0, e)
        return None
    if r.stop_reason in ("refusal", "max_tokens"):
        log.warning("model stopped: %s", r.stop_reason)
        return None
    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        return None
    out["_seconds"] = round(time.monotonic() - t0, 1)
    return out


def parallel(jobs, deadline_s):
    """{key: result or None} for {key: (system, user, schema)}, all started at once, each cut off
    at the shared deadline."""
    start = time.monotonic()
    futures = {k: POOL.submit(call, s, u, sch, deadline_s) for k, (s, u, sch) in jobs.items()}
    wait(futures.values(), timeout=deadline_s + 1)
    out = {}
    for k, f in futures.items():
        out[k] = f.result() if f.done() else None
    log.info("%d model calls in %.1fs", len(jobs), time.monotonic() - start)
    return out
