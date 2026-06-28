from datetime import datetime, timedelta, timezone

from app.money import D, ZERO
from app.models import ListingState, ShippingOption, BidResult, STRATEGIES
from .factories import make_state


def test_strategy_list():
    assert set(STRATEGIES) == {"slow", "adaptive", "blocking", "fast"}


class TestListingState:
    def test_is_leader_true(self):
        assert make_state(leader_is_me=True, my_id=7).is_leader is True

    def test_is_leader_false_when_other(self):
        assert make_state(leader_is_me=False).is_leader is False

    def test_logged_out_never_leader(self):
        s = make_state(leader_is_me=True)
        s.my_member_id = None
        assert s.logged_in is False
        assert s.is_leader is False

    def test_seconds_left_and_closed(self):
        s = make_state(seconds_left=50)
        assert 45 < s.seconds_left() <= 50
        assert s.is_closed is False

    def test_closed_in_past(self):
        s = make_state(seconds_left=-5)
        assert s.is_closed is True

    def test_cheapest_dearest(self):
        s = make_state()
        assert s.cheapest_shipping().price == D(9)
        assert s.dearest_shipping().price == D(22)

    def test_shipping_none_when_empty(self):
        s = make_state(shipping=[])
        assert s.cheapest_shipping() is None
        assert s.dearest_shipping() is None

    def test_has_bids(self):
        assert make_state(bid_count=2).has_bids is True
        assert make_state(bid_count=0).has_bids is False


def test_shipping_option_model():
    so = ShippingOption(shipping_id="4", method="Auckland", price=D(9))
    assert so.shipping_id == "4" and so.price == D(9)


def test_bid_result_defaults():
    r = BidResult(ok=True, message="ok")
    assert r.amount == ZERO and r.autobid is False and r.submitted is False
