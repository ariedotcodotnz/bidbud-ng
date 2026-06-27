"""Pure-logic self-tests (no network, no Playwright, no FastAPI).

Run with:  .venv/bin/python -m tests.test_core
Verifies money math, the #frend-state listing parser, and every strategy's
decisions across the key scenarios.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.money import D, one_increment, default_two_increment_bid, add_cents
from app.models import ListingState
from app.strategies import (
    StrategyConfig, StrategyMemory,
    decide_fast, decide_slow, decide_blocking, decide_adaptive,
)
from app.trademe import listing as L

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def make_state(seconds_left, current, min_next, leader_is_me,
               bid_count=1, my_id=1):
    end = datetime.now(timezone.utc) + timedelta(seconds=seconds_left)
    return ListingState(
        listing_id="1", title="t", end_date=end,
        current_price=D(current), min_next_bid=D(min_next), start_price=D(1),
        bid_count=bid_count, reserve_met=True, reserve_state=1,
        leading_bidder_id=(my_id if leader_is_me else 999),
        my_member_id=my_id, shipping_options=[],
    )


def test_money():
    print("money")
    check("one_increment", one_increment(D(1), D("1.5")) == D("0.50"))
    check("default_two_increment",
          default_two_increment_bid(D(1), D("1.5")) == D(2))
    nudged = add_cents(D(10), max_bid=D(11), dont_add_cents=False)
    check("add_cents nudges round dollar", nudged > D(10) and nudged < D(11))
    check("add_cents respects max",
          add_cents(D(10), max_bid=D(10), dont_add_cents=False) == D(10))
    check("dont_add_cents off",
          add_cents(D(10), max_bid=D(11), dont_add_cents=True) == D(10))


def test_parser():
    print("listing parser")
    html = (
        '<html><body>'
        '<script id="frend-state" type="application/json">'
        '{"NGRX_STATE":{"currentMember":{"item":{"memberId":9594617}},'
        '"listing":{"cachedDetails":{"entities":{"6006426545":{"item":{'
        '"listingId":6006426545,"title":"Apple MacBook",'
        '"endDate":"__date__:2030-06-30T08:00:00.000Z",'
        '"startPrice":1,"minimumNextBidAmount":1.5,"bidCount":1,'
        '"isReserveMet":true,"reserveState":1,"maxBidAmount":1,'
        '"allowsPickups":3,"hasPing":true,'
        '"bids":{"totalCount":1,"list":[{"bidAmount":1,'
        '"bidder":{"memberId":8154893}}]},'
        '"shippingOptions":['
        '{"shippingId":4,"price":9,"method":"Auckland, Standard"},'
        '{"shippingId":6,"price":22,"method":"South Island"}]}}}}}}}'
        '</script></body></html>'
    )
    s = L.parse_state(html, "6006426545")
    check("parsed", s is not None)
    check("title", s.title == "Apple MacBook")
    check("current_price", s.current_price == D(1))
    check("min_next_bid", s.min_next_bid == D("1.5"))
    check("logged_in", s.logged_in is True)
    check("not leader (other bidder)", s.is_leader is False)
    check("reserve met", s.reserve_met is True)
    check("end date future / not closed", s.is_closed is False)
    check("cheapest shipping is Auckland $9",
          s.cheapest_shipping().price == D(9))
    check("two shipping options", len(s.shipping_options) == 2)
    check("listing id from url",
          L.listing_id_from_url("https://x/listing/6006426545") == "6006426545")


def test_fast():
    print("fast")
    cfg = StrategyConfig(max_bid=D(50), fast_lead_seconds=120)
    mem = StrategyMemory()
    far = decide_fast(make_state(300, 1, "1.5", False), mem, cfg)
    check("waits before T-2min", far.action == "wait")
    near = decide_fast(make_state(100, 1, "1.5", False), mem, cfg)
    check("places autobid max in window", near.action == "place"
          and near.autobid and near.amount == D(50) and near.is_max)
    poor = StrategyConfig(max_bid=D(1))
    check("won't bid above max",
          decide_fast(make_state(100, 1, "1.5", False), StrategyMemory(),
                      poor).action == "wait")


def test_slow():
    print("slow")
    cfg = StrategyConfig(max_bid=D(50), snipe_seconds=8)
    check("leading -> wait",
          decide_slow(make_state(5, 1, "1.5", True), StrategyMemory(), cfg)
          .action == "wait")
    check("before snipe -> wait",
          decide_slow(make_state(30, 1, "1.5", False), StrategyMemory(), cfg)
          .action == "wait")
    d = decide_slow(make_state(5, 1, "1.5", False), StrategyMemory(), cfg)
    check("snipe -> min bid", d.action == "place" and d.amount == D("1.5")
          and not d.autobid)


def test_blocking():
    print("blocking")
    cfg = StrategyConfig(max_bid=D(50))
    d = decide_blocking(make_state(60, 1, "1.5", False), StrategyMemory(), cfg)
    check("autobid one increment above leader",
          d.action == "place" and d.autobid and d.amount == D("1.5"))
    check("leading -> wait (hold)",
          decide_blocking(make_state(60, 1, "1.5", True), StrategyMemory(), cfg)
          .action == "wait")
    capped = decide_blocking(make_state(60, 49, "49.5", False),
                             StrategyMemory(), StrategyConfig(max_bid=D("49.5")))
    check("capped at max", capped.amount == D("49.5"))


def test_adaptive():
    print("adaptive")
    cfg = StrategyConfig(max_bid=D(50), snipe_seconds=8)
    mem = StrategyMemory()
    probe = decide_adaptive(make_state(60, 1, "1.5", False), mem, cfg)
    check("first action is a probe", probe.is_probe and probe.action == "place")
    mem.awaiting_probe_result = True
    # Still not leading after probe -> autobid detected -> bid max.
    aggressive = decide_adaptive(make_state(59, 2, "2.5", False), mem, cfg)
    check("detects autobid then bids max",
          aggressive.action == "place" and aggressive.autobid
          and aggressive.amount == D(50))
    # Fresh game where probe made us the leader -> no autobid.
    mem2 = StrategyMemory(awaiting_probe_result=True)
    calm = decide_adaptive(make_state(60, 2, "2.5", True), mem2, cfg)
    check("no autobid path when probe won the lead", calm.action == "wait")


if __name__ == "__main__":
    test_money()
    test_parser()
    test_fast()
    test_slow()
    test_blocking()
    test_adaptive()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
