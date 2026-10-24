"""The Live Final loop against the simulator: waves show up one after another in the case list, every
case of a wave plays within its cap, a failed poll is simply repeated, a crashed case is resumed and
the loop stops once the last wave has closed. No network beyond loopback, no model."""

import secrets

from arena_kit import ArenaClient
from arena_kit.client import ArenaError

from hackit2 import session, settings


class ScriptedFinalClient(ArenaClient):
    """The simulator client with a scripted final: `cases("final")` answers 403 before the first wave,
    then lists each wave's cases as open with time left and later as closed; one poll fails outright."""

    def __init__(self, url, api_key, waves):
        super().__init__(url, api_key, rate=1000)
        self.waves, self.poll_count = waves, 0

    def cases(self, set_="practice"):
        if set_ != "final":
            return super().cases(set_)
        self.poll_count += 1
        if self.poll_count == 1:
            raise ArenaError(403, "the final has not started")
        if self.poll_count == 3:
            raise ArenaError(500, "a hiccup")
        current_wave = 1 + (self.poll_count - 2) // 2  # a new wave every second poll; one past the last closes it
        listing = []
        for wave, case_ids in enumerate(self.waves, start=1):
            if wave > current_wave:
                continue
            is_open = wave == current_wave
            for case_id in case_ids:
                listing.append(
                    {"case_id": case_id, "wave": wave, "open": is_open, "seconds_left": 480 if is_open else 0}
                )
        return listing


def final_client(arena_url, waves):
    api_key = ArenaClient(arena_url).register(f"test-{secrets.token_hex(3)}", "sim")["api_key"]
    return ScriptedFinalClient(arena_url, api_key, waves)


def test_run_final_plays_every_case_of_every_wave(arena_url, tmp_path, monkeypatch):
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    waves = [["case:S01", "case:S02"], ["case:S03"]]
    outcomes = session.run_final(final_client(arena_url, waves), waves=2, parallel=3, poll=0.05)
    assert set(outcomes) == {"case:S01", "case:S02", "case:S03"}
    for case_id, outcome in outcomes.items():
        assert not isinstance(outcome, Exception), (case_id, outcome)
        assert "deal" in outcome


def test_crashing_case_is_reopened_while_the_cap_allows(arena_url, tmp_path, monkeypatch):
    """A transient failure (a network blip, a malformed reply) must not lose a final case: it is opened
    again while the cap leaves time, and it still closes."""
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    monkeypatch.setattr(settings, "RESUME_DELAY_S", 0)
    genuine_run_case, crashes = session.run_case, {"remaining": 3}

    def crash_a_few_times(*args, **kwargs):
        if crashes["remaining"] > 0:
            crashes["remaining"] -= 1
            raise RuntimeError("blip")
        return genuine_run_case(*args, **kwargs)

    monkeypatch.setattr(session, "run_case", crash_a_few_times)
    outcomes = session.run_final(final_client(arena_url, [["case:S01"]]), waves=1, parallel=1, poll=0.05)
    assert crashes["remaining"] == 0
    assert "deal" in outcomes["case:S01"]


def test_case_that_never_stops_crashing_reports_its_exception(arena_url, tmp_path, monkeypatch):
    monkeypatch.setattr(session, "RUN_LOGS", tmp_path)
    monkeypatch.setattr(settings, "RESUME_DELAY_S", 0)
    monkeypatch.setattr(settings, "RESUME_ATTEMPTS_MAX", 3)
    attempts = {"count": 0}

    def always_crash(*args, **kwargs):
        attempts["count"] += 1
        raise RuntimeError("down")

    monkeypatch.setattr(session, "run_case", always_crash)
    outcomes = session.run_final(final_client(arena_url, [["case:S01"]]), waves=1, parallel=1, poll=0.05)
    assert attempts["count"] == 3
    assert isinstance(outcomes["case:S01"], RuntimeError)
