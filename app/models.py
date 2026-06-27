"""Typed value objects shared across modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from .money import D, ZERO


# The four BidBud strategies.
STRATEGIES = ("slow", "adaptive", "blocking", "fast")
STRATEGY_LABELS = {
    "slow": "Slow",
    "adaptive": "Adaptive",
    "blocking": "Blocking",
    "fast": "Fast",
}


@dataclass
class ShippingOption:
    shipping_id: str
    method: str
    price: Decimal


@dataclass
class ListingState:
    """A snapshot of a listing parsed from the embedded ``#frend-state`` JSON."""

    listing_id: str
    title: str
    end_date: datetime              # tz-aware UTC
    current_price: Decimal
    min_next_bid: Decimal
    start_price: Decimal
    bid_count: int
    reserve_met: bool
    reserve_state: int
    leading_bidder_id: int | None
    my_member_id: int | None
    shipping_options: list[ShippingOption] = field(default_factory=list)
    allows_pickups: bool = False
    has_ping: bool = False
    raw: dict | None = None

    # -- derived ----------------------------------------------------------- #
    @property
    def logged_in(self) -> bool:
        return self.my_member_id is not None

    @property
    def is_leader(self) -> bool:
        return (
            self.my_member_id is not None
            and self.leading_bidder_id is not None
            and self.leading_bidder_id == self.my_member_id
        )

    @property
    def has_bids(self) -> bool:
        return self.bid_count > 0

    def seconds_left(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (self.end_date - now).total_seconds()

    @property
    def is_closed(self) -> bool:
        return self.seconds_left() <= 0

    def cheapest_shipping(self) -> ShippingOption | None:
        if not self.shipping_options:
            return None
        return min(self.shipping_options, key=lambda s: s.price)

    def dearest_shipping(self) -> ShippingOption | None:
        if not self.shipping_options:
            return None
        return max(self.shipping_options, key=lambda s: s.price)


@dataclass
class BidResult:
    ok: bool                # verified the bid took effect
    message: str
    amount: Decimal = ZERO
    autobid: bool = False
    submitted: bool = False  # we clicked submit without an explicit rejection
