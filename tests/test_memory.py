"""Learning between cases: the stop recorded per thread, a gradual and capped pull of the factory
prior that only ever makes us more careful, only ever a wider spread, separate sets, and no shift on
any error."""

from types import SimpleNamespace

from buyer import config, memory

CASE = {"set": "final", "type": "renewal", "buyer": {"category_goal": "routine"}}


def neg(*threads):
    return SimpleNamespace(threads={f"sup:{i}": t for i, t in enumerate(threads)})


def thread(theirs, q0=100.0, status="final_offer"):
    return SimpleNamespace(ours=[90.0], q0=q0, theirs=[q0, theirs], last_status=status, prof={"signals": []})


def test_records_only_threads_that_reached_their_last_word():
    rows = memory.record(CASE, neg(thread(93.0), thread(95.0, status="open"), thread(96.0, status="agreed")))
    assert [r["share"] for r in rows] == [0.93, 0.96]
    assert memory.observed("final", "renewal") == [0.93, 0.96]


def test_the_accepted_sheet_counts_as_its_stop():
    rows = memory.record(CASE, neg(thread(94.0, status="open")), {"deal": True, "supplier_id": "sup:0"})
    assert [(r["share"], r["status"]) for r in rows] == [(0.94, "accepted")]


def test_no_data_no_change():
    assert memory.adjust(CASE, 0.975, 0.02) == (0.975, 0.02, None)


def test_the_pull_up_is_gradual_and_capped():
    factory = config.FLOOR_PRIOR["renewal"][0]
    memory.record(CASE, neg(thread(99.5)))
    _, _, note1 = memory.adjust(CASE, factory, 0.02)
    assert note1["shift"] == 0.0  # one case a few points up is noise
    case = {**CASE, "type": "rfq"}
    for _ in range(2):
        memory.record(case, neg(thread(95.0)))
    _, _, note_small = memory.adjust(case, 0.84, 0.07)
    expected = (config.MEMORY_N0 * 0.84 + 2 * 0.95) / (config.MEMORY_N0 + 2) - 0.84
    assert abs(note_small["shift"] - min(expected, config.MEMORY_MAX_SHIFT)) < 1e-4
    for _ in range(20):
        memory.record(case, neg(thread(99.0)))
    mean, _, note2 = memory.adjust(case, 0.84, 0.07)
    assert note2["shift"] == config.MEMORY_MAX_SHIFT
    assert abs(mean - (0.84 + config.MEMORY_MAX_SHIFT)) < 1e-9


def test_softer_suppliers_never_make_us_greedier():
    for _ in range(10):
        memory.record(CASE, neg(thread(80.0)))
    mean, _, note = memory.adjust(CASE, 0.975, 0.02)
    assert note["shift"] == -config.MEMORY_MAX_DOWN and mean == 0.975 - config.MEMORY_MAX_DOWN


def test_the_spread_only_widens_and_sets_stay_apart():
    for share in (85.0, 95.0, 99.0):
        memory.record(CASE, neg(thread(share)))
    _, sd, _ = memory.adjust(CASE, 0.975, 0.02)
    assert sd > 0.02
    _, sd_tight, _ = memory.adjust(CASE, 0.975, 0.5)
    assert sd_tight == 0.5
    assert memory.adjust({**CASE, "set": "practice"}, 0.975, 0.02)[2] is None


def test_a_broken_file_is_ignored():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    memory.path("final").write_text('not json\n{"type": "renewal", "share": "x"}\n', encoding="utf-8")
    assert memory.adjust(CASE, 0.975, 0.02) == (0.975, 0.02, None)
