"""Per-job bidding engine.

One :func:`run_job` coroutine runs per watched auction for its whole lifetime.
It polls listing state (cheaply, via the authenticated request context), feeds
it to the chosen strategy, and executes the resulting bid through the bid modal.

Poll cadence scales with time-to-close so far-off auctions are gentle on
TradeMe while the closing seconds are watched tightly.
"""
from __future__ import annotations

import asyncio
import json

from playwright.async_api import Page

from . import db
from .money import D, add_cents, fmt, one_increment
from .models import BidResult, ListingState
from .strategies import StrategyConfig, StrategyMemory, decide
from .trademe import bidder
from .trademe import listing as listing_mod
from .trademe.browser import browser


def _poll_interval(seconds_left: float, opts: dict) -> float:
    if seconds_left <= 15:
        return float(opts.get("poll_final_seconds", 1))
    if seconds_left <= 60:
        return float(opts.get("poll_near_seconds", 2))
    if seconds_left <= 180:
        return float(opts.get("poll_far_seconds", 5))
    if seconds_left <= 600:
        return 15.0
    return 60.0


def _resolve_shipping(state: ListingState, choice: str):
    """Resolve a per-job shipping choice against the live listing options.

    ``choice`` is either a specific TradeMe ``shippingId`` (str) that the user
    picked, or one of the keywords ``cheapest`` / ``dearest`` / ``none``.
    Returns ``(index, method)`` to drive the bid modal, or ``(None, None)``.
    """
    if choice == "pickup" and state and state.allows_pickups:
        # Pick-up isn't in shippingOptions; the bidder selects it in the modal
        # by matching the radio whose label reads "Pick-up".
        return None, "Pick-up"
    if choice == "pickup":
        raise ValueError("pick-up is no longer available on this listing")
    opts = state.shipping_options if state else []
    if not opts or choice == "none":
        return None, None
    if choice == "cheapest":
        target = state.cheapest_shipping()
    elif choice == "dearest":
        target = state.dearest_shipping()
    else:  # a specific shippingId
        target = next((o for o in opts if str(o.shipping_id) == str(choice)), None)
        if target is None:                      # option vanished – be safe
            target = state.cheapest_shipping()
    if target is None:
        return None, None
    try:
        return opts.index(target), target.method
    except ValueError:
        return 0, target.method


async def run_job(job_id: int) -> None:
    job = db.get_job(job_id)
    if not job or job["status"] not in ("scheduled", "active"):
        return

    opts = json.loads(job["options"] or "{}")
    url = job["url"]
    listing_id = job["listing_id"]
    max_bid = D(job["max_bid"])
    strategy = job["strategy"]

    cfg = StrategyConfig(
        max_bid=max_bid,
        activate_seconds=120,
        snipe_seconds=int(opts.get("snipe_seconds", 8)),
        fast_lead_seconds=int(opts.get("fast_lead_seconds", 120)),
    )
    mem = StrategyMemory()
    shipping_choice = (opts.get("shipping_choice")
                       or opts.get("shipping_preference", "cheapest"))
    dont_add_cents = bool(int(opts.get("dont_add_cents", 0)))
    email_if_outbid = bool(int(opts.get("email_if_outbid", 1)))
    bid_early = bool(int(opts.get("bid_early_single_bid", 0)))
    placed_early = False

    db.update_job(job_id, status="active")
    db.log(job_id, "info", f"Engine started ({strategy}, max {fmt(max_bid)}).")

    page: Page | None = None
    fetch_fails = 0

    async def get_page() -> Page:
        nonlocal page
        if page is None or page.is_closed():
            page = await browser.new_page()
        return page

    try:
        while True:
            current = db.get_job(job_id)
            if not current or current["status"] not in ("active", "scheduled"):
                return  # cancelled/deleted elsewhere

            html = await browser.fetch_html(url)
            state = listing_mod.parse_state(html, listing_id) if html else None
            if state is None:
                fetch_fails += 1
                db.update_job(job_id, last_action="couldn't read listing state")
                if fetch_fails >= 30:
                    db.log(job_id, "error", "Giving up: listing unreadable.")
                    db.update_job(job_id, status="error",
                                  last_action="listing unreadable")
                    return
                await asyncio.sleep(5)
                continue
            fetch_fails = 0

            if not state.logged_in:
                db.update_job(job_id, last_action="waiting for TradeMe login")
                await asyncio.sleep(20)
                continue

            # Denormalise latest state for the dashboard.
            db.update_job(
                job_id,
                title=state.title or job["title"],
                end_date=state.end_date.isoformat(),
                current_price=str(state.current_price),
                min_next_bid=str(state.min_next_bid),
                bid_count=state.bid_count,
                is_leader=1 if state.is_leader else 0,
                reserve_met=1 if state.reserve_met else 0,
            )

            if state.is_closed:
                won = state.is_leader
                status = "won" if won else "lost"
                note = (f"Auction closed – {'WON' if won else 'lost'} at "
                        f"{fmt(state.current_price)}.")
                db.update_job(job_id, status=status, last_action=note)
                db.log(job_id, "info", note)
                return

            seconds_left = state.seconds_left()

            # "Bid early if single bid left": if the price has reached the point
            # where one more bid would land at your maximum, lodge your max now
            # (so you get in first at that price) instead of waiting.
            inc = one_increment(state.current_price, state.min_next_bid)
            afford = D(state.min_next_bid) <= max_bid
            one_before = (max_bid - D(state.min_next_bid)) < inc
            if (bid_early and not placed_early and not state.is_leader
                    and afford and one_before):
                placed_early = True
                await _execute(
                    job_id, await get_page(), url, listing_id, max_bid,
                    autobid=True, is_max=True, choice=shipping_choice,
                    dont_add_cents=dont_add_cents, email_if_outbid=email_if_outbid,
                    max_bid=max_bid, reason="bid-early: one bid from your max",
                )
                await asyncio.sleep(_poll_interval(seconds_left, opts))
                continue

            decision = decide(strategy, state, mem, cfg)
            db.update_job(job_id, last_action=decision.reason)

            if decision.action == "place":
                res = await _execute(
                    job_id, await get_page(), url, listing_id, decision.amount,
                    autobid=decision.autobid, is_max=decision.is_max,
                    choice=shipping_choice,
                    dont_add_cents=dont_add_cents, email_if_outbid=email_if_outbid,
                    max_bid=max_bid, reason=decision.reason,
                )
                placed = res.ok or res.submitted
                if decision.is_probe:
                    mem.awaiting_probe_result = True
                # Avoid re-lodging a max autobid every poll once it's in.
                if placed and strategy == "fast":
                    mem.placed_fast = True
                if placed and decision.is_max and decision.autobid:
                    mem.placed_max = True

            await asyncio.sleep(_poll_interval(seconds_left, opts))
    except asyncio.CancelledError:
        db.log(job_id, "info", "Engine cancelled.")
        raise
    except Exception as exc:  # noqa: BLE001
        db.log(job_id, "error", f"Engine crashed: {exc}")
        db.update_job(job_id, status="error", last_action=f"crash: {exc}")
    finally:
        if page is not None and not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass


async def _execute(
    job_id, page, url, listing_id, amount, *, autobid, is_max, choice,
    dont_add_cents, email_if_outbid, max_bid, reason,
):
    amount = D(amount)

    def failed(message: str) -> BidResult:
        result = BidResult(
            ok=False, message=message, amount=amount, autobid=autobid,
            submitted=False,
        )
        db.update_job(job_id, last_action=result.message)
        db.log(job_id, "warn", result.message)
        return result

    if not autobid and not is_max:
        amount = add_cents(amount, max_bid, dont_add_cents)
    ship_state = listing_mod.parse_state(
        await browser.fetch_html(url) or "", listing_id
    )
    if ship_state is None:
        if choice == "pickup":
            return failed(
                "Pick-up was selected, but listing delivery options could not be refreshed.",
            )
        ship_idx, ship_method = None, None
    else:
        try:
            ship_idx, ship_method = _resolve_shipping(ship_state, choice)
        except ValueError as exc:
            return failed(str(exc))

    db.log(job_id, "info", f"Placing {fmt(amount)} "
                           f"({'autobid' if autobid else 'normal'}) – {reason}")
    result = await bidder.place_bid(
        page, url, amount=amount, autobid=autobid, shipping_index=ship_idx,
        shipping_method=ship_method, email_if_outbid=email_if_outbid,
        listing_id=listing_id,
    )
    db.update_job(job_id, last_action=result.message)
    db.log(job_id, "info" if result.ok else "warn", result.message)
    return result
