"""Plays cases with the negotiation engine.

The arena serves one call per session at a time and each thread's 60-second clock runs from the
supplier's reply. So one loop sends one message at a time, always to the thread whose clock has run
longest; settled threads get a short question before their clock runs out while the others still
bargain; finally the cheapest acceptable sheet is accepted. `run_final` is the Live Final loop."""

import functools
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from arena_kit.client import ArenaError
from arena_kit.play import (
    CLOSE_BEFORE_CAP_S,
    ENDED,
    ROUND_MARGIN_S,
    RemoteTable,
    fetch_documents,
    latest,
    moves_left,
    quota_used_up,
)

from . import lessons, model, settings
from .mailer import keepalive_message
from .negotiation import FINISHED, SETTLED, Negotiator, SupplierThread
from .prep import briefing

log = logging.getLogger("hackit2")
RUN_LOGS = Path("logs/runs")
LIVE_LOGS = Path("logs/live")


def needs_keepalive(age, round_trip_s, write_s):
    """True when a waiting offer must be refreshed before another model-plus-send cycle could exhaust
    its supplier's clock."""
    projected = age + max(0, write_s) + round_trip_s
    return age > settings.KEEPALIVE_AFTER_S or projected >= settings.THREAD_CLOCK_S - settings.CLOCK_RESERVE_S


def writing_budget(supplier_id, replied_at, live, round_trip_s, now):
    """Seconds the model may take on the message to `supplier_id`: its own clock must not run out, and
    every other live thread must still get a message once this send returns."""
    usable = settings.THREAD_CLOCK_S - settings.CLOCK_RESERVE_S
    limits = [usable - (now - replied_at[supplier_id])]
    limits += [usable - round_trip_s - (now - replied_at[other]) for other in live if other != supplier_id]
    return min(settings.MODEL_TIMEOUT_S, *limits)


def near_deadline(deadline, margin=90):
    cap = deadline() if callable(deadline) else deadline
    return bool(cap) and time.time() > cap - margin


def run_case(table, case_id, pkg=None, deadline=None, complete=None, team=True, learn=True):
    """Play one case to its end and return the arena's result.

    `team`: the preparation team briefs the negotiator before the session opens (it needs the case
    file on `table.case`) and the reply reader reads every reply; off, the plain price engine plays.
    `learn`: the debrief records this case's lesson for later cases."""
    briefs = None
    if team and complete:
        briefs = briefs_or_empty(table.case, pkg, complete)
    opened_at = time.monotonic()  # the supplier clocks run from their first e-mail, before our setup
    case, threads = table.open(case_id)
    if getattr(table, "done", None):  # a resumed case that had already ended
        return table.done
    for events in threads.values():
        for message in events:
            read_attachments(table, case_id, message)
    negotiator = Negotiator(case, pkg, complete, briefs)
    negotiator.live_journal = open_live_journal(case_id)
    negotiator.log_event(
        None,
        "session",
        session_id=getattr(table, "session_id", None),
        title=case.get("title"),
        type=case.get("type"),
        goal=case["buyer"].get("category_goal"),
        team=briefs is not None,
        suppliers=list(threads),
    )
    for supplier_id, events in threads.items():
        try:
            negotiator.open_thread(supplier_id, events)
            if any(m.get("from") == "buyer" for m in events):  # resumed: continue the path
                negotiator.resume_thread(supplier_id, events)
        except Exception:  # one supplier's data must not lose the case
            log.exception("%s %s: thread setup failed", case_id, supplier_id)
            negotiator.suppliers.setdefault(supplier_id, SupplierThread(supplier_id)).phase = SETTLED
    failures = dict.fromkeys(threads, 0)
    replied_at = dict.fromkeys(threads, opened_at)
    round_trip_s = settings.FIRST_ROUND_TRIP_S  # the slowest send so far
    # round one goes out cheapest quote first: if a clock runs out, it is the least promising one's
    first_round_order = sorted(threads, key=lambda sid: negotiator.suppliers[sid].quote_at_plan or float("inf"))
    rank = {sid: i for i, sid in enumerate(first_round_order)}
    result = None

    def is_live(supplier_id):
        return latest(threads[supplier_id]).get("status") not in ENDED

    def bargaining():
        return [s for s in threads if negotiator.suppliers[s].phase not in FINISHED and is_live(s)]

    # the longest round so far (our move plus the supplier's reply): a wave cap must leave room for one
    # more, or a round started in time ends after the cap without a deal
    slowest_round = 0.0
    while not near_deadline(deadline, max(CLOSE_BEFORE_CAP_S, slowest_round + ROUND_MARGIN_S)):
        round_started = time.monotonic()
        negotiator.retire_outclassed(threads)
        queue = sorted(bargaining(), key=lambda s: (replied_at[s], rank[s]))
        if not queue:
            break
        waiting = [
            s
            for s in threads
            if negotiator.suppliers[s].phase == SETTLED
            and not negotiator.suppliers[s].outclassed
            and is_live(s)
            and replied_at[s] < replied_at[queue[0]]
            and needs_keepalive(time.monotonic() - replied_at[s], round_trip_s, settings.MODEL_TIMEOUT_S)
            and moves_left(case, threads[s]) - negotiator.suppliers[s].late_rounds > 0
        ]
        if waiting:  # a settled bidder whose clock runs out first: keep it alive
            supplier_id = min(waiting, key=lambda s: replied_at[s])
            message = keepalive_message(threads[supplier_id])
        else:
            supplier_id = queue[0]
            live = [s for s in threads if is_live(s)]
            budget = writing_budget(supplier_id, replied_at, live, round_trip_s, time.monotonic())
            try:
                move = negotiator.next_message(supplier_id, threads, budget)
            except Exception:  # an unexpected field: the thread must not fall silent
                log.exception("%s %s: move failed", case_id, supplier_id)
                failures[supplier_id] += 1
                if failures[supplier_id] > settings.MOVE_FAILURES_MAX:
                    negotiator.suppliers[supplier_id].phase = SETTLED
                    continue
                move = negotiator.safe_message(supplier_id, threads)
            if move is None:
                continue
            message = move["message"]
        sent_at = time.monotonic()
        try:
            reply = table.send(supplier_id, message)
            round_trip_s = max(round_trip_s, time.monotonic() - sent_at)
            slowest_round = max(slowest_round, time.monotonic() - round_started)
        except ArenaError as exc:
            if exc.status != 409:
                raise
            slowest_round = max(slowest_round, time.monotonic() - round_started)
            log.info("%s %s: %s", case_id, supplier_id, exc.detail)
            negotiator.undo_delivery(supplier_id)  # it never got this message: our last offer stands
            reply = table.missed(supplier_id, exc)
            if "result" in reply:
                result = reply["result"]
                break
            threads[supplier_id].append(reply)
            replied_at[supplier_id] = time.monotonic()
            continue
        negotiator.confirm_delivery(supplier_id)
        if "result" in reply:  # the reply ended the case, e.g. the last supplier walked away
            result = reply["result"]
            break
        threads[supplier_id] += [{**message, "from": "buyer"}, reply]
        replied_at[supplier_id] = time.monotonic()
    if result is None:
        for events in threads.values():  # the latest quote PDFs: standard terms for the close
            for message in events[-2:]:
                read_attachments(table, case_id, message)
        result = settle(table, case_id, negotiator, threads)
    negotiator.log_event(
        result.get("supplier_id"),
        "end",
        session_id=table.session_id,
        deal=result.get("deal"),
        score=result.get("score"),
        end_reason=result.get("end_reason"),
    )
    write_run_log(table.session_id, case_id, negotiator, result)
    if learn and briefs is not None:
        try:
            lessons.record_lesson(case, negotiator, result, complete)
        except Exception:  # the case is closed: a failed debrief costs nothing
            log.exception("%s: debrief failed", case_id)
    return result


def open_live_journal(case_id):
    """The file a case's decisions stream into while it runs (`logs/live/case_<id>.jsonl`), for the
    live dashboard; None when it cannot be created."""
    try:
        LIVE_LOGS.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return LIVE_LOGS / f"case_{case_id.split(':')[-1]}.jsonl"


def read_attachments(table, case_id, message):
    """A supplier message's attachments as text. A file the arena does not deliver or the reader cannot
    parse is skipped: its terms then come from the e-mail and the case file, and the case goes on."""
    try:
        fetch_documents(table, message)
    except Exception as exc:  # noqa: BLE001 - a document must never cost the case
        log.warning("%s: document not read: %r", case_id, exc)


def briefs_or_empty(case, pkg, complete):
    """The preparation team's briefs, or {} (no case file before the open, or the team failed); the
    reply reader still reads the replies."""
    if not case:
        return {}
    try:
        return briefing.prepare_briefs(case, pkg, complete)
    except Exception:  # the team must never cost the case
        log.exception("%s: preparation failed", case.get("case_id"))
        return {}


def settle(table, case_id, negotiator, threads):
    """Accept or walk away. A 409 (the sheet expired, the thread ended) means the arena state moved:
    re-read what stands now and decide again with the same guards. Anything else raises, and the Live
    Final loop resumes the case."""
    for _ in range(settings.CLOSE_ATTEMPTS):
        decision = negotiator.final_decision(threads)
        supplier_id = decision["supplier_id"]
        try:
            return table.close(supplier_id, decision["message"])
        except ArenaError as exc:
            if exc.status != 409:
                raise
            log.info("%s %s: close refused: %s", case_id, supplier_id, exc.detail)
            reply = table.missed(supplier_id, exc)
            if "result" in reply:
                return reply["result"]
            threads[supplier_id].append(reply)
    raise RuntimeError(f"{case_id}: no close after {settings.CLOSE_ATTEMPTS} attempts")


def write_run_log(session_id, case_id, negotiator, result):
    RUN_LOGS.mkdir(parents=True, exist_ok=True)
    keep = ("deal", "supplier_id", "terms", "c_open", "c_deal", "c_best", "score")
    path = RUN_LOGS / f"{case_id.split(':')[-1]}_{session_id}.json"
    record = {
        "case": case_id,
        "session": session_id,
        "journal": negotiator.journal,
        "result": {k: result.get(k) for k in keep},
    }
    path.write_text(json.dumps(record, indent=1, default=str))


class PrefetchedTable(RemoteTable):
    """A practice or sandbox table that knows its case file before the session opens (the preparation
    team needs it). Only the final resumes a running session, so a refused open raises as it is."""

    def open(self, case_id):
        opened = self.client.open_session(case_id)
        self.session_id = opened["session_id"]
        log.info("session %s", self.session_id)
        return opened["case"], opened["threads"]


def run_batch(client, cases, pkg=None, parallel=8, complete=None, team=True):
    """Practice or sandbox: many case files at once. {case_id: result, or the exception that stopped it}."""
    quota_gone = threading.Event()
    files = {case["case_id"]: case for case in cases}
    before = model.counters()

    def one(case_id):
        if quota_gone.is_set():
            return case_id, ArenaError(429, "not started: the practice quota is used up")
        try:
            return case_id, run_case(
                PrefetchedTable(client, files[case_id]), case_id, pkg, complete=complete, team=team
            )
        except ArenaError as exc:
            if quota_used_up(exc):
                quota_gone.set()
            log.warning("%s: %s", case_id, exc)
            return case_id, exc
        except Exception as exc:  # one broken case must not stop the others
            log.exception("%s failed", case_id)
            return case_id, exc

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        outcomes = dict(pool.map(one, files))
    log.info("model reliability across %d cases: %s", len(files), model.counters_since(before))
    return outcomes


def run_final(client, pkg=None, waves=5, parallel=10, poll=None, complete=None, team=True):
    """The Live Final: wait for each wave and play its cases in parallel within the wave's cap. No human
    may restart it, so nothing here raises: a failed poll is polled again and a crashed case is resumed
    while its cap leaves time to close (the arena names the running session on a second open). The
    final records no lessons."""
    poll = settings.WAVE_POLL_S if poll is None else poll
    started, closed_waves, caps = {}, set(), {}

    def one(case, deadline):
        attempt = 0
        while True:
            attempt += 1
            try:
                return run_case(RemoteTable(client, case), case["case_id"], pkg, deadline, complete, team, False)
            except Exception as exc:  # noqa: BLE001 - one broken case must not stop the others
                log.exception("%s failed (attempt %d)", case["case_id"], attempt)
                error = exc
            if attempt >= settings.RESUME_ATTEMPTS_MAX or near_deadline(deadline, settings.RESUME_MARGIN_S):
                return error
            time.sleep(settings.RESUME_DELAY_S)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while len(closed_waves) < waves or not all(f.done() for f in started.values()):
            try:
                cases = client.cases("final")
            except Exception as exc:  # noqa: BLE001 - 403 before the first wave, a network blip: poll again
                if not (isinstance(exc, ArenaError) and exc.status == 403):
                    log.warning("case list: %s", exc)
                cases = []
            for case in cases:
                if not case.get("open"):
                    closed_waves.add(case["wave"])
                    continue
                if case.get("seconds_left") is not None:
                    caps[case["wave"]] = time.time() + case["seconds_left"]
                if case["case_id"] not in started and case.get("seconds_left"):
                    log.warning("wave %s: %s", case["wave"], case["case_id"])
                    started[case["case_id"]] = pool.submit(one, case, functools.partial(caps.get, case["wave"]))
            time.sleep(poll)
    return {case_id: future.result() for case_id, future in started.items()}
