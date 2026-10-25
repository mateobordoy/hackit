"""Plays cases. The arena answers one call per session at a time and every thread's 60-second
clock runs from the supplier's reply, so one loop sends one message at a time, always to the
thread that has waited longest; settled bidders get a short question before their clock runs out
while others still negotiate; then the cheapest acceptable sheet is accepted (or we walk).
`play_waves` is the Live Final: it never raises, and resumes a crashed case while its cap allows."""

import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from arena_kit.client import ArenaError
from arena_kit.play import ENDED, RemoteTable, fetch_documents, latest, moves_left, quota_used_up

from . import config, llm, memory, records, telemetry, writer
from .datapkg import load_package
from .policy import Negotiator
from .thread import Thread

log = logging.getLogger("buyer")


def keepalive_due(age, send_s, writer_s):
    return age > config.KEEPALIVE_AFTER_S or age + max(0, writer_s) + send_s >= config.CLOCK_S - config.MARGIN_S


def writer_budget(sid, replied, live, send_s, now):
    """Seconds the model may take for `sid`: its own clock must hold, and every other live thread
    must still get a message once this send returns."""
    left = [config.CLOCK_S - config.MARGIN_S - (now - replied[sid])]
    left += [config.CLOCK_S - config.MARGIN_S - send_s - (now - replied[o]) for o in live if o != sid]
    return max(0.0, min(config.LLM_TIMEOUT_S, *left))


def deadline_near(deadline, margin):
    cap = deadline() if callable(deadline) else deadline
    return bool(cap) and time.time() > cap - margin


def fetch(table, message):
    try:
        fetch_documents(table, message)
    except Exception as e:  # noqa: BLE001 - a document must never cost the case
        log.warning("document not read: %r", e)


class PracticeTable(RemoteTable):
    """Practice and sandbox: a refused open raises as it is (only the final resumes)."""

    def open(self, case_id):
        r = self.client.open_session(case_id)
        self.session_id = r["session_id"]
        log.info("session %s", self.session_id)
        return r["case"], r["threads"]


def play_case(table, case_id, pkg=None, deadline=None):
    opened = time.monotonic()
    case, threads = table.open(case_id)
    if getattr(table, "done", None):
        return table.done
    for events in threads.values():
        for m in events:
            fetch(table, m)
    telemetry.bind(case_id)
    telemetry.case_start(case, getattr(table, "session_id", None), threads)
    neg = Negotiator(case, pkg)
    neg.prefetch(threads)  # the reader agent reads every supplier at once
    for sid, events in threads.items():
        try:
            neg.setup(sid, events)
            if any(m.get("from") == "buyer" for m in events):
                restore(neg, sid, events)
        except Exception:  # one supplier's data must not lose the case
            log.exception("%s %s: setup failed", case_id, sid)
            neg.threads.setdefault(sid, Thread(sid)).state = "done"
    failed = dict.fromkeys(threads, 0)
    replied = dict.fromkeys(threads, opened)
    send_s, slowest, result = config.FIRST_SEND_S, 0.0, None
    order = sorted(threads, key=lambda s: neg.threads[s].q0 or float("inf"))  # cheapest quote first
    rank = {s: i for i, s in enumerate(order)}

    def live(sid):
        return latest(threads[sid]).get("status") not in ENDED

    while not deadline_near(deadline, max(config.CLOSE_BEFORE_CAP_S, slowest + config.ROUND_MARGIN_S)):
        round_t0 = time.monotonic()
        neg.prune(threads)
        todo = sorted(
            (s for s in threads if neg.threads[s].state not in ("done", "ended") and live(s)),
            key=lambda s: (replied[s], rank[s]),
        )
        if not todo:
            break
        waiting = [
            s
            for s in threads
            if neg.threads[s].state == "done"
            and not neg.threads[s].dominated
            and live(s)
            and replied[s] < replied[todo[0]]
            and keepalive_due(time.monotonic() - replied[s], send_s, config.LLM_TIMEOUT_S if llm.enabled() else 0)
            and moves_left(case, threads[s]) - neg.threads[s].late > 0
        ]
        if waiting:
            sid = min(waiting, key=lambda s: replied[s])
            message = writer.keepalive_message(threads[sid], case)
            neg.note(sid, "keepalive")
        else:
            sid = todo[0]
            budget = writer_budget(sid, replied, [s for s in threads if live(s)], send_s, time.monotonic())
            try:
                move = neg.move(sid, threads, budget)
            except Exception:  # the thread must not fall silent
                log.exception("%s %s: move failed", case_id, sid)
                failed[sid] += 1
                if failed[sid] > 2:
                    neg.threads[sid].state = "done"
                    continue
                move = neg.fallback_move(sid, threads)
            if move is None:
                continue
            message = move["message"]
        t0 = time.monotonic()
        telemetry.message_out(sid, message)
        try:
            reply = table.send(sid, message)
            send_s = max(send_s, time.monotonic() - t0)
            slowest = max(slowest, time.monotonic() - round_t0)
        except ArenaError as e:
            if e.status == 422:  # invalid: nothing happened, no round used
                log.error("%s %s: refused %s", case_id, sid, e.detail)
                neg.note(sid, "refused", detail=str(e.detail))
                if neg.threads[sid].pending:
                    neg.rollback(sid)
                    neg.threads[sid].late -= 1  # a refusal is not a round
                neg.threads[sid].state = "done"
                continue
            if e.status != 409:
                raise
            slowest = max(slowest, time.monotonic() - round_t0)
            log.info("%s %s: %s", case_id, sid, e.detail)
            neg.rollback(sid)
            reply = table.missed(sid, e)
            telemetry.emit("timeout", supplier=sid, detail=str(e.detail))
            if "result" in reply:
                result = reply["result"]
                break
            telemetry.message_in(sid, reply)
            threads[sid].append(reply)
            replied[sid] = time.monotonic()
            continue
        neg.commit(sid)
        if "result" in reply:
            result = reply["result"]
            break
        fetch(table, reply)
        telemetry.message_in(sid, reply)
        threads[sid] += [{**message, "from": "buyer"}, reply]
        replied[sid] = time.monotonic()
    if result is None:
        for events in threads.values():  # the latest quote PDFs: standard terms for the close
            for m in events[-2:]:
                fetch(table, m)
        result = close(table, case_id, neg, threads)
    telemetry.emit("end", result={k: (result or {}).get(k) for k in records.KEEP})
    telemetry.unbind()
    records.save(table.session_id, case, neg, result)
    try:
        memory.record(case, neg, result)  # where these suppliers stopped: the next case of its type learns
    except Exception:  # noqa: BLE001 - learning must never cost the result
        log.exception("%s: memory not recorded", case_id)
    return result


def restore(neg, sid, events):
    """A resumed session: rebuild what we sent and what it answered, so we continue the path."""
    t = neg.threads[sid]
    if t.state == "ended":
        return
    for m in events:
        if m.get("from") == "buyer":
            neg.used.update(m.get("refs") or [])
            sheets = m.get("term_sheets") or []
            if sheets:
                t.ours.append(float(sheets[0]["terms"]["unit_price"]))
                t.last_sent_terms = sheets[0]["terms"]
        elif t.ours:
            sh = [s for s in m.get("term_sheets") or [] if (s.get("terms") or {}).get("unit_price") is not None]
            if sh:
                t.theirs.append(float(sh[0]["terms"]["unit_price"]))
    if t.ours:
        t.terms = {**t.last_sent_terms}
        t.sent = None
        if events and events[-1].get("from") != "buyer" and len(t.theirs) > 1:
            t.theirs.pop()  # the latest reply is read by move() like any fresh one
    neg.note(sid, "restore", ours=t.ours, theirs=t.theirs)


def close(table, case_id, neg, threads):
    for _ in range(config.CLOSE_TRIES):
        decision = neg.close(threads)
        sid = decision["supplier_id"]
        telemetry.message_out(sid, decision["message"])
        try:
            return table.close(sid, decision["message"])
        except ArenaError as e:
            if e.status != 409:
                raise
            log.info("%s %s: close refused: %s", case_id, sid, e.detail)
            reply = table.missed(sid, e)
            if "result" in reply:
                return reply["result"]
            threads[sid].append(reply)
    return table.result()


def play_batch(client, cases, repeat=1, parallel=5):
    """Practice or sandbox: every case file `repeat` times. Returns [(case_id, result or error)]."""
    pkg = load_package()
    used_up = threading.Event()
    jobs = [c for c in cases for _ in range(repeat)]

    def one(case):
        cid = case["case_id"]
        if used_up.is_set():
            return cid, ArenaError(429, "not started: the practice quota is used up")
        try:
            return cid, play_case(PracticeTable(client, case), cid, pkg)
        except ArenaError as e:
            if quota_used_up(e):
                used_up.set()
            log.warning("%s: %s", cid, e)
            return cid, e
        except Exception as e:  # noqa: BLE001
            log.exception("%s failed", cid)
            return cid, e

    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 9))) as pool:
        return list(pool.map(one, jobs))


def play_waves(client, waves=5, parallel=10, poll=2.0):
    """The Live Final: wait for each wave, play its cases in parallel within its cap."""
    pkg = load_package()
    started, closed, caps = {}, set(), {}

    def one(case, deadline):
        attempt, err = 0, None
        while True:
            attempt += 1
            try:
                return play_case(RemoteTable(client, case), case["case_id"], pkg, deadline)
            except Exception as e:  # noqa: BLE001 - one broken case must not stop the others
                log.exception("%s failed (attempt %d)", case["case_id"], attempt)
                err = e
            if attempt >= config.RETRY_MAX or deadline_near(deadline, config.RETRY_MARGIN_S):
                return err
            time.sleep(config.RETRY_S)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while len(closed) < waves or not all(f.done() for f in started.values()):
            try:
                cases = client.cases("final")
            except Exception as e:  # noqa: BLE001 - 403 before the first wave, a blip: poll again
                if not (isinstance(e, ArenaError) and e.status == 403):
                    log.warning("case list: %s", e)
                cases = []
            for case in cases:
                if not case.get("open"):
                    closed.add(case.get("wave"))
                    continue
                if case.get("seconds_left") is not None:
                    caps[case["wave"]] = time.time() + case["seconds_left"]
                if case["case_id"] not in started and case.get("seconds_left"):
                    log.warning("wave %s: %s", case["wave"], case["case_id"])
                    started[case["case_id"]] = pool.submit(one, case, functools.partial(caps.get, case["wave"]))
            time.sleep(poll)
    return {cid: f.result() for cid, f in started.items()}
