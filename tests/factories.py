"""Test data builders shared across the suite."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models import ListingState, ShippingOption
from app.money import D

DEFAULT_SHIPPING = [
    ("4", "Auckland, Standard", 9),
    ("5", "North Island, Standard", 17),
    ("6", "South Island, Economy", 22),
]


def _tm_date(dt: datetime) -> str:
    return "__date__:" + dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def frend_html(
    *,
    listing_id: str = "6006426545",
    member_id: int | None = 1,
    title: str = "Apple MacBook",
    end_dt: datetime | None = None,
    start_price=1,
    min_next=1.5,
    current=None,
    bid_count: int = 1,
    reserve_met: bool = True,
    reserve_state: int = 1,
    leader_id: int | None = 8154893,
    shipping=DEFAULT_SHIPPING,
    extra_item: dict | None = None,
) -> str:
    """Render a TradeMe-style page with an embedded ``#frend-state`` script."""
    end_dt = end_dt or (datetime.now(timezone.utc) + timedelta(hours=1))
    if current is None:
        current = float(start_price) if bid_count == 0 else 1
    bids = {"totalCount": bid_count, "list": []}
    if bid_count and leader_id is not None:
        bids["list"] = [{"bidAmount": current, "bidder": {"memberId": leader_id}}]

    item = {
        "listingId": int(listing_id),
        "title": title,
        "endDate": _tm_date(end_dt),
        "startPrice": start_price,
        "minimumNextBidAmount": min_next,
        "maxBidAmount": current,
        "bidCount": bid_count,
        "isReserveMet": reserve_met,
        "reserveState": reserve_state,
        "allowsPickups": 3,
        "hasPing": True,
        "bids": bids,
        "shippingOptions": [
            {"shippingId": int(sid), "method": method, "price": price}
            for sid, method, price in (shipping or [])
        ],
    }
    if extra_item:
        item.update(extra_item)

    current_member = {"item": ({"memberId": member_id} if member_id else {})}
    state = {
        "NGRX_STATE": {
            "currentMember": current_member,
            "listing": {"cachedDetails": {"entities": {listing_id: {"item": item}}}},
        }
    }
    blob = json.dumps(state)
    return (
        "<html><body>"
        f'<script id="frend-state" type="application/json">{blob}</script>'
        "</body></html>"
    )


def make_state(
    *,
    seconds_left: float = 90,
    current="1",
    min_next="1.5",
    start="1",
    leader_is_me: bool = False,
    bid_count: int = 1,
    my_id: int = 1,
    shipping=DEFAULT_SHIPPING,
    reserve_met: bool = True,
    allows_pickups: bool = False,
) -> ListingState:
    end = datetime.now(timezone.utc) + timedelta(seconds=seconds_left)
    opts = [ShippingOption(str(s), m, D(p)) for s, m, p in (shipping or [])]
    return ListingState(
        listing_id="1", title="T", end_date=end,
        current_price=D(current), min_next_bid=D(min_next), start_price=D(start),
        bid_count=bid_count, reserve_met=reserve_met, reserve_state=1,
        leading_bidder_id=(my_id if leader_is_me else 999),
        my_member_id=my_id, shipping_options=opts,
        allows_pickups=allows_pickups,
    )


class ListingSim:
    """A controllable auction used by engine tests.

    ``html()`` renders current state for the patched ``browser.fetch_html``;
    ``advance()`` moves the clock toward close; ``apply_bid()`` simulates a bid
    taking effect (used by the patched ``bidder.place_bid``).
    """

    def __init__(
        self,
        *,
        listing_id: str = "6006426545",
        my_id: int = 1,
        remaining: float = 90,
        current="1",
        min_next="1.5",
        bid_count: int = 1,
        leader_id: int | None = 999,
        shipping=DEFAULT_SHIPPING,
        logged_in: bool = True,
    ):
        self.listing_id = listing_id
        self.my_id = my_id
        self.remaining = float(remaining)
        self.current = D(current)
        self.min_next = D(min_next)
        self.bid_count = bid_count
        self.leader_id = leader_id
        self.shipping = shipping
        self.logged_in = logged_in
        self.fetches = 0

    def advance(self, secs: float) -> None:
        self.remaining = max(self.remaining - max(secs, 0.001), -1)

    def html(self) -> str:
        self.fetches += 1
        end = datetime.now(timezone.utc) + timedelta(seconds=self.remaining)
        return frend_html(
            listing_id=self.listing_id,
            member_id=(self.my_id if self.logged_in else None),
            end_dt=end,
            current=float(self.current),
            min_next=float(self.min_next),
            bid_count=self.bid_count,
            leader_id=self.leader_id,
            shipping=self.shipping,
        )

    def apply_bid(self, amount, autobid: bool) -> None:
        """Make us the leader at ``amount`` (and bump the minimum next bid)."""
        self.current = D(amount)
        self.leader_id = self.my_id
        self.bid_count += 1
        self.min_next = D(amount) + D("0.50")


class DummyPage:
    """Minimal stand-in for a Playwright Page (engine 'placing' path)."""

    def __init__(self):
        self._closed = False

    def is_closed(self):
        return self._closed

    async def close(self):
        self._closed = True

    @property
    def url(self):
        return "about:blank"
