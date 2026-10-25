from buyer import config, planner
from buyer.belief import AGREED, FINAL, OPEN, Belief, State, respond


def play(true, belief, xs):
    """Feed the belief the replies a supplier with parameters `true` gives to our prices `xs`."""
    st = State(1, 1.0, None)
    for x in xs:
        status, c, nxt = respond(true, st, x)
        belief.update(st, x, status, c)
        if status != OPEN:
            break
        st = nxt
    return st


def test_belief_finds_the_floor_from_the_counters():
    true = [0.86, 0.4, 0.35, 0.5, 0.004, 2, 0.1]
    b = Belief(0.92, 0.05)
    play(true, b, [0.78, 0.81, 0.83, 0.845])
    assert abs(b.quantile(0.5) - 0.86) < 0.025
    assert b.quantile(0.9) <= b.ceiling + 0.003


def test_respond_agrees_when_our_sheet_reaches_its_next_counter():
    p = [0.9, 0.5, 0.3, 0.5, 0.004, 2, 0.1]
    st = State(2, 0.95, 0.85)
    target = 0.95 - 0.3 * (0.95 - 0.9)
    assert respond(p, st, target)[0] == AGREED
    assert respond(p, st, 0.851)[0] == FINAL  # a token move
    assert respond(p, st, 0.87)[0] == OPEN


def ctx(**kw):
    base = dict(moves_left=6, cost_of=lambda x: 1000 * x, walk_cost=1300.0, cap=None, accept_cost=None)
    return planner.Context(**{**base, **kw})


def test_planner_stays_between_our_last_price_and_its_counter_and_under_the_cap():
    b = Belief(0.9, 0.04)
    st = State(3, 0.95, 0.85, gap_sum=0.2)
    for cap in (None, 0.9):
        d = planner.decide(b, st, ctx(cap=cap))
        assert d.kind == "offer"
        assert st.x_prev * (1 + config.MIN_MOVE) - 1e-9 <= d.price <= st.c_prev * config.MAX_SHARE_OF_COUNTER
        if cap:
            assert d.price <= cap


def test_planner_accepts_only_after_it_has_seen_the_supplier_move():
    b = Belief(0.905, 0.003)  # it is sure the supplier sits at its floor
    early = State(2, 0.906, 0.86, gap_sum=0.05)
    assert planner.decide(b, early, ctx(accept_cost=906.0)).kind == "offer"  # one reply is too little to know
    later = State(4, 0.906, 0.86, gap_sum=0.05)
    assert planner.decide(b, later, ctx(accept_cost=906.0)).kind in ("accept", "offer")
    no_room = State(2, 0.9005, 0.899)  # within a token move of its counter: take it
    assert planner.decide(b, no_room, ctx(accept_cost=900.5)).kind == "accept"


def test_planner_prefers_a_move_that_keeps_a_patient_supplier_conceding():
    b = Belief(0.85, 0.02, patience=(0.45, 0.5))
    st = State(2, 0.97, 0.78, gap_sum=0.2)
    d = planner.decide(b, st, ctx())
    assert d.price < 0.9  # it does not jump to the counter while the floor is far below
