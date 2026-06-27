from datetime import datetime, timedelta, timezone

import pytest

from app.money import D
from app.trademe import listing as L
from .factories import frend_html


class TestUrlHelpers:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.trademe.co.nz/a/marketplace/x/listing/6006426545", "6006426545"),
        ("https://www.trademe.co.nz/a/.../listing/123?foo=bar", "123"),
        ("no-listing-here", None),
    ])
    def test_listing_id_from_url(self, url, expected):
        assert L.listing_id_from_url(url) == expected

    def test_normalise_bare_number(self):
        assert L.normalise_url("6006426545").endswith("/listing/6006426545")

    def test_normalise_passthrough_url(self):
        u = "https://www.trademe.co.nz/a/x/listing/1"
        assert L.normalise_url(u) == u


class TestParseTmDate:
    def test_parses_date_marker(self):
        dt = L._parse_tm_date("__date__:2030-06-30T08:00:00.000Z")
        assert dt == datetime(2030, 6, 30, 8, 0, tzinfo=timezone.utc)

    def test_returns_none_for_garbage(self):
        assert L._parse_tm_date("not a date") is None
        assert L._parse_tm_date(None) is None


class TestExtractState:
    def test_missing_script_returns_none(self):
        assert L.extract_state_json("<html></html>") is None

    def test_bad_json_returns_none(self):
        html = ('<script id="frend-state" type="application/json">'
                '{not json}</script>')
        assert L.extract_state_json(html) is None


class TestParseState:
    def test_full_listing(self):
        html = frend_html(member_id=9594617, leader_id=8154893,
                          current=1, min_next=1.5, bid_count=1)
        s = L.parse_state(html, "6006426545")
        assert s is not None
        assert s.title == "Apple MacBook"
        assert s.current_price == D(1)
        assert s.min_next_bid == D("1.5")
        assert s.start_price == D(1)
        assert s.bid_count == 1
        assert s.reserve_met is True
        assert s.my_member_id == 9594617
        assert s.leading_bidder_id == 8154893
        assert s.logged_in is True
        assert s.is_leader is False
        assert len(s.shipping_options) == 3
        assert s.cheapest_shipping().price == D(9)
        assert s.dearest_shipping().price == D(22)

    def test_logged_out_has_no_member(self):
        s = L.parse_state(frend_html(member_id=None), "6006426545")
        assert s.logged_in is False
        assert s.is_leader is False

    def test_leader_is_me(self):
        s = L.parse_state(frend_html(member_id=42, leader_id=42), "6006426545")
        assert s.is_leader is True

    def test_no_bids_uses_start_price(self):
        s = L.parse_state(
            frend_html(bid_count=0, start_price=5, min_next=5, current=5),
            "6006426545",
        )
        assert s.bid_count == 0
        assert s.has_bids is False
        assert s.current_price == D(5)
        assert s.leading_bidder_id is None

    def test_falls_back_to_first_entity_when_id_missing(self):
        html = frend_html(listing_id="999")
        # Ask for a different id; parser falls back to the only cached entity.
        s = L.parse_state(html, "does-not-match")
        assert s is not None
        assert s.listing_id == "999"

    def test_closed_when_end_in_past(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        s = L.parse_state(frend_html(end_dt=past), "6006426545")
        assert s.is_closed is True

    def test_returns_none_without_state(self):
        assert L.parse_state("<html>no state</html>", "1") is None

    @pytest.mark.parametrize("value,expected", [
        (0, False),   # None
        (1, True),    # Allow
        (2, True),    # Demand (pickup only)
        (3, False),   # Forbid  <- the real listing had this
        (None, False),
    ])
    def test_allows_pickups_enum(self, value, expected):
        html = frend_html(extra_item={"allowsPickups": value})
        assert L.parse_state(html, "6006426545").allows_pickups is expected
