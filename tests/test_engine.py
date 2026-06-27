"""Engine tests: pure helpers + full ``run_job`` flows with everything mocked.

We patch ``browser.fetch_html`` (returns the simulator's HTML), ``browser.new_page``,
``bidder.place_bid`` (records calls / mutates the simulator) and ``asyncio.sleep``
(advances the simulated clock instantly), so a whole auction plays out in
milliseconds with no Chromium.
"""
from __future__ import annotations

import pytest

from app import db, engine
from app.models import BidResult
from app.money import D
from app.trademe import bidder
from app.trademe import browser as browser_mod
from .factories import ListingSim, DummyPage, make_state


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_poll_interval_thresholds():
    o = {"poll_final_seconds": 1, "poll_near_seconds": 2, "poll_far_seconds": 5}
    assert engine._poll_interval(5, o) == 1
    assert engine._poll_interval(30, o) == 2
    assert engine._poll_interval(120, o) == 5
    assert engine._poll_interval(400, o) == 15
    assert engine._poll_interval(1000, o) == 60


class TestResolveShipping:
    def test_specific_id(self):
        assert engine._resolve_shipping(make_state(), "5") == (1, "North Island, Standard")

    def test_cheapest(self):
        assert engine._resolve_shipping(make_state(), "cheapest") == (0, "Auckland, Standard")

    def test_dearest(self):
        assert engine._resolve_shipping(make_state(), "dearest") == (2, "South Island, Economy")

    def test_none(self):
        assert engine._resolve_shipping(make_state(), "none") == (None, None)

    def test_missing_id_falls_back_to_cheapest(self):
        assert engine._resolve_shipping(make_state(), "99") == (0, "Auckland, Standard")

    def test_no_shipping_options(self):
        assert engine._resolve_shipping(make_state(shipping=[]), "cheapest") == (None, None)


# --------------------------------------------------------------------------- #
# run_job flows
# --------------------------------------------------------------------------- #
def _job_opts(**over):
    base = {
        "enter_default_bid": "1", "bid_early_single_bid": "0", "dont_add_cents": "0",
        "email_if_outbid": "1", "shipping_preference": "cheapest",
        "shipping_choice": "cheapest", "snipe_seconds": "8", "fast_lead_seconds": "120",
        "poll_far_seconds": "5", "poll_near_seconds": "2", "poll_final_seconds": "1",
    }
    base.update(over)
    return base


def _patch(monkeypatch, sim, *, place_effect=True, sleep_hook=None):
    bm = browser_mod.browser

    async def fetch(url, *a, **k):
        return sim.html()

    async def new_page():
        return DummyPage()

    calls = []

    async def fake_place(page, url, *, amount, autobid, shipping_index,
                         shipping_method=None, email_if_outbid, listing_id=None):
        calls.append({
            "amount": amount, "autobid": autobid,
            "shipping_index": shipping_index, "shipping_method": shipping_method,
        })
        if place_effect:
            sim.apply_bid(amount, autobid)
        return BidResult(True, "ok", amount=amount, autobid=autobid, submitted=True)

    n = {"i": 0}

    async def fake_sleep(secs):
        n["i"] += 1
        if n["i"] > 3000:
            raise RuntimeError("runaway engine loop")
        if sleep_hook:
            sleep_hook(n["i"], sim)
        sim.advance(secs)

    monkeypatch.setattr(bm, "fetch_html", fetch)
    monkeypatch.setattr(bm, "new_page", new_page)
    monkeypatch.setattr(bidder, "place_bid", fake_place)
    monkeypatch.setattr(engine.asyncio, "sleep", fake_sleep)
    return calls


def _mkjob(strategy, max_bid, **opts):
    return db.create_job(
        listing_id="6006426545",
        url="https://www.trademe.co.nz/a/marketplace/listing/6006426545",
        title="T", strategy=strategy, max_bid=max_bid,
        end_date=None, current_price=None, options=_job_opts(**opts),
    )


async def test_fast_places_max_and_wins(temp_db, monkeypatch):
    sim = ListingSim(remaining=90, current="1", min_next="1.5", leader_id=999)
    calls = _patch(monkeypatch, sim)
    jid = _mkjob("fast", "50")

    await engine.run_job(jid)

    job = db.get_job(jid)
    assert job["status"] == "won"
    assert len(calls) == 1                       # lodged max exactly once
    assert calls[0]["autobid"] is True
    assert calls[0]["amount"] == D(50)
    assert calls[0]["shipping_index"] == 0
    assert calls[0]["shipping_method"] == "Auckland, Standard"


async def test_unaffordable_never_bids_and_loses(temp_db, monkeypatch):
    sim = ListingSim(remaining=60, current="1", min_next="1.5", leader_id=999)
    calls = _patch(monkeypatch, sim)
    jid = _mkjob("slow", "1.00")                 # max below the minimum next bid

    await engine.run_job(jid)

    job = db.get_job(jid)
    assert job["status"] == "lost"
    assert calls == []


async def test_bid_early_lodges_max_before_window(temp_db, monkeypatch):
    # One increment (0.4) away from the $10 max, but well outside the 2-min window.
    sim = ListingSim(remaining=300, current="9.6", min_next="10", leader_id=999)
    calls = _patch(monkeypatch, sim)
    jid = _mkjob("slow", "10", bid_early_single_bid="1")

    await engine.run_job(jid)

    job = db.get_job(jid)
    assert calls, "expected an early bid"
    assert calls[0]["amount"] == D(10) and calls[0]["autobid"] is True
    assert job["status"] == "won"


async def test_recovers_after_being_logged_out(temp_db, monkeypatch):
    sim = ListingSim(remaining=90, current="1", min_next="1.5",
                     leader_id=999, logged_in=False)

    def hook(i, s):
        if i == 1:                               # log in after the first wait
            s.logged_in = True

    calls = _patch(monkeypatch, sim, sleep_hook=hook)
    jid = _mkjob("fast", "50")

    await engine.run_job(jid)

    job = db.get_job(jid)
    assert job["status"] == "won"
    assert len(calls) == 1


async def test_normal_bid_gets_cents_nudged(temp_db, monkeypatch):
    # Slow places a *normal* min bid; with a round-dollar amount and cents
    # enabled, the executed amount should be nudged above the round figure.
    sim = ListingSim(remaining=5, current="9", min_next="10", leader_id=999)
    calls = _patch(monkeypatch, sim, place_effect=True)
    jid = _mkjob("slow", "20", dont_add_cents="0", snipe_seconds="8")

    await engine.run_job(jid)

    assert calls, "expected a snipe bid"
    amt = calls[0]["amount"]
    assert calls[0]["autobid"] is False
    assert D(10) < amt < D(11)                   # $10.xx, nudged but under max
