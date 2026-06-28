"""Typed value objects and SQLModel tables shared across the app."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

from .money import ZERO


# The four BidBud strategies.
STRATEGIES = ("slow", "adaptive", "blocking", "fast")
STRATEGY_LABELS = {
    "slow": "Slow",
    "adaptive": "Adaptive",
    "blocking": "Blocking",
    "fast": "Fast",
}


# --------------------------------------------------------------------------- #
# Pydantic runtime models
# --------------------------------------------------------------------------- #
class ShippingOption(BaseModel):
    shipping_id: str
    method: str
    price: Decimal


class ListingState(BaseModel):
    """A snapshot of a listing parsed from the embedded ``#frend-state`` JSON."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

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
    shipping_options: list[ShippingOption] = PydanticField(default_factory=list)
    allows_pickups: bool = False
    has_ping: bool = False
    raw: dict[str, Any] | None = None

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


class BidResult(BaseModel):
    ok: bool                # verified the bid took effect
    message: str
    amount: Decimal = ZERO
    autobid: bool = False
    submitted: bool = False  # we clicked submit without an explicit rejection


# --------------------------------------------------------------------------- #
# SQLModel tables
# --------------------------------------------------------------------------- #
class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str | None = None


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    listing_id: str
    url: str
    title: str | None = None
    strategy: str
    max_bid: str
    status: str = "scheduled"
    end_date: str | None = None
    current_price: str | None = None
    min_next_bid: str | None = None
    bid_count: int = Field(default=0, nullable=True)
    is_leader: int = Field(default=0, nullable=True)
    reserve_met: int = Field(default=0, nullable=True)
    last_action: str | None = None
    options: str | None = Field(default=None, sa_column=Column(Text))
    created_at: str | None = None
    updated_at: str | None = None


class BidLog(SQLModel, table=True):
    __tablename__ = "bid_log"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = None
    ts: str
    level: str
    message: str = Field(sa_column=Column(Text, nullable=False))
