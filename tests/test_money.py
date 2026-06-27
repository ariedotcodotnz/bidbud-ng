from decimal import Decimal

import pytest

from app.money import (
    D, ZERO, one_increment, default_two_increment_bid, add_cents, fmt,
)


class TestD:
    @pytest.mark.parametrize("raw,expected", [
        (1, "1.00"), (1.5, "1.50"), ("2.5", "2.50"), (Decimal("3"), "3.00"),
        (None, "0.00"), ("0.005", "0.01"),  # rounds half up
    ])
    def test_coerces_to_2dp(self, raw, expected):
        assert D(raw) == Decimal(expected)

    def test_zero_constant(self):
        assert ZERO == Decimal("0.00")


class TestIncrement:
    def test_one_increment_basic(self):
        assert one_increment(D(1), D("1.5")) == D("0.50")

    def test_one_increment_large_band(self):
        assert one_increment(D(100), D(102)) == D(2)

    def test_one_increment_fallback_when_nonpositive(self):
        # If min_next <= current, fall back to the smallest standard step.
        assert one_increment(D(5), D(5)) == D("0.50")
        assert one_increment(D(5), D(4)) == D("0.50")

    def test_default_two_increment(self):
        assert default_two_increment_bid(D(1), D("1.5")) == D(2)
        assert default_two_increment_bid(D(100), D(102)) == D(104)


class TestAddCents:
    def test_nudges_round_dollar_within_max(self):
        out = add_cents(D(10), max_bid=D(11), dont_add_cents=False)
        assert D(10) < out < D(11)
        assert out != out.to_integral_value()  # has cents

    def test_respects_max(self):
        assert add_cents(D(10), max_bid=D(10), dont_add_cents=False) == D(10)

    def test_disabled(self):
        assert add_cents(D(10), max_bid=D(11), dont_add_cents=True) == D(10)

    def test_leaves_non_round_amounts_alone(self):
        assert add_cents(D("10.50"), max_bid=D(20), dont_add_cents=False) == D("10.50")

    def test_never_exceeds_max_even_with_random(self):
        for _ in range(200):
            out = add_cents(D(10), max_bid=D("10.20"), dont_add_cents=False)
            assert out <= D("10.20")


def test_fmt():
    assert fmt(D("1")) == "$1.00"
    assert fmt(D("1234.5")) == "$1234.50"
