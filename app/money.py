"""Money + bid-increment helpers.

All monetary values inside the app are :class:`decimal.Decimal` to avoid
float rounding. TradeMe works in NZD with cents.
"""
from __future__ import annotations

import random
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def D(value) -> Decimal:
    """Coerce ints/floats/strings/None into a 2dp Decimal."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def one_increment(current: Decimal, min_next: Decimal) -> Decimal:
    """The size of a single bid step in the current price band.

    TradeMe exposes ``minimumNextBidAmount`` for the listing, so the cleanest
    way to know the current increment is simply ``min_next - current``.
    """
    inc = D(min_next) - D(current)
    if inc <= ZERO:
        # Fallback to the smallest standard TradeMe increment.
        inc = Decimal("0.50")
    return inc


def default_two_increment_bid(current: Decimal, min_next: Decimal) -> Decimal:
    """The "Enter default bid" value: two increments above the current bid."""
    inc = one_increment(current, min_next)
    return D(current) + inc + inc


def add_cents(amount: Decimal, max_bid: Decimal, dont_add_cents: bool) -> Decimal:
    """Optionally nudge a round-dollar amount up by a few cents.

    Mirrors BidBud's "a few extra cents might win you the auction" behaviour.
    Never pushes the bid above ``max_bid``.
    """
    amount = D(amount)
    if dont_add_cents:
        return amount
    if amount != amount.to_integral_value():
        # Already has cents; leave it alone.
        return amount
    extra = Decimal(random.randint(1, 49)) * CENT
    nudged = amount + extra
    if nudged > D(max_bid):
        return amount
    return nudged


def fmt(amount: Decimal) -> str:
    return f"${D(amount):.2f}"
