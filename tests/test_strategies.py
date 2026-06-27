from app.money import D
from app.strategies import (
    StrategyConfig, StrategyMemory,
    decide_fast, decide_slow, decide_blocking, decide_adaptive, decide,
)
from .factories import make_state


def cfg(max_bid="50", **kw):
    return StrategyConfig(max_bid=D(max_bid), **kw)


class TestFast:
    def test_waits_before_window(self):
        d = decide_fast(make_state(seconds_left=300), StrategyMemory(), cfg())
        assert d.action == "wait"

    def test_places_max_autobid_in_window(self):
        d = decide_fast(make_state(seconds_left=100), StrategyMemory(), cfg())
        assert d.action == "place" and d.autobid and d.is_max
        assert d.amount == D(50)

    def test_does_not_rebid_once_placed(self):
        mem = StrategyMemory(placed_fast=True)
        assert decide_fast(make_state(seconds_left=100), mem, cfg()).action == "wait"

    def test_wont_bid_above_max(self):
        d = decide_fast(make_state(seconds_left=100, min_next="2"),
                        StrategyMemory(), cfg(max_bid="1"))
        assert d.action == "wait"


class TestSlow:
    def test_leader_waits(self):
        d = decide_slow(make_state(seconds_left=5, leader_is_me=True),
                        StrategyMemory(), cfg())
        assert d.action == "wait"

    def test_waits_before_window(self):
        assert decide_slow(make_state(seconds_left=300), StrategyMemory(),
                           cfg()).action == "wait"

    def test_waits_before_snipe(self):
        assert decide_slow(make_state(seconds_left=30), StrategyMemory(),
                           cfg(snipe_seconds=8)).action == "wait"

    def test_snipes_minimum_bid(self):
        d = decide_slow(make_state(seconds_left=5), StrategyMemory(),
                        cfg(snipe_seconds=8))
        assert d.action == "place" and not d.autobid and d.amount == D("1.5")

    def test_cannot_afford(self):
        d = decide_slow(make_state(seconds_left=5, min_next="100"),
                        StrategyMemory(), cfg(max_bid="50"))
        assert d.action == "wait"


class TestBlocking:
    def test_autobid_one_increment_above_leader(self):
        d = decide_blocking(make_state(seconds_left=60), StrategyMemory(), cfg())
        assert d.action == "place" and d.autobid and d.is_max
        assert d.amount == D("1.5")  # current 1 + 0.5 increment

    def test_leader_holds(self):
        d = decide_blocking(make_state(seconds_left=60, leader_is_me=True),
                            StrategyMemory(), cfg())
        assert d.action == "wait"

    def test_waits_before_window(self):
        assert decide_blocking(make_state(seconds_left=300), StrategyMemory(),
                               cfg()).action == "wait"

    def test_capped_at_max(self):
        d = decide_blocking(make_state(seconds_left=60, current="49", min_next="49.5"),
                            StrategyMemory(), cfg(max_bid="49.5"))
        assert d.amount == D("49.5")

    def test_cannot_afford(self):
        d = decide_blocking(make_state(seconds_left=60, min_next="100"),
                            StrategyMemory(), cfg(max_bid="50"))
        assert d.action == "wait"


class TestAdaptive:
    def test_first_action_is_probe(self):
        d = decide_adaptive(make_state(seconds_left=60), StrategyMemory(), cfg())
        assert d.is_probe and d.action == "place" and d.amount == D("1.5")

    def test_detects_autobid_then_bids_max(self):
        mem = StrategyMemory(awaiting_probe_result=True)
        # still not leading after probe -> autobid present -> bid max
        d = decide_adaptive(make_state(seconds_left=59, current="2", min_next="2.5",
                                       leader_is_me=False), mem, cfg())
        assert mem.autobid_detected is True
        assert d.action == "place" and d.autobid and d.amount == D(50)

    def test_maxed_out_against_higher_autobid(self):
        mem = StrategyMemory(autobid_detected=True, placed_max=True)
        d = decide_adaptive(make_state(seconds_left=30), mem, cfg())
        assert d.action == "wait"

    def test_no_autobid_holds_then_snipes(self):
        # probe made us the leader -> no autobid
        mem = StrategyMemory(awaiting_probe_result=True)
        calm = decide_adaptive(make_state(seconds_left=60, leader_is_me=True),
                               mem, cfg())
        assert mem.autobid_detected is False
        assert calm.action == "wait"
        # later, no autobid + closing seconds -> snipe minimum
        mem2 = StrategyMemory(autobid_detected=False)
        snipe = decide_adaptive(make_state(seconds_left=5), mem2, cfg(snipe_seconds=8))
        assert snipe.action == "place" and not snipe.autobid

    def test_holds_back_during_bidding_war(self):
        mem = StrategyMemory(autobid_detected=False, last_bid_count=1)
        d = decide_adaptive(make_state(seconds_left=60, bid_count=5),
                            mem, cfg(snipe_seconds=8))
        assert d.action == "wait"

    def test_waits_before_window(self):
        assert decide_adaptive(make_state(seconds_left=300), StrategyMemory(),
                               cfg()).action == "wait"


class TestDispatch:
    def test_routes_to_named_strategy(self):
        d = decide("slow", make_state(seconds_left=5), StrategyMemory(), cfg())
        assert d.action == "place" and not d.autobid

    def test_unknown_defaults_to_fast(self):
        d = decide("bogus", make_state(seconds_left=100), StrategyMemory(), cfg())
        assert d.autobid and d.is_max
