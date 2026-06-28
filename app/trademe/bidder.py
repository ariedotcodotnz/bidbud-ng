"""Place a bid by driving TradeMe's bid modal.

The listing page renders an auction box (``tm-listing-auction``) with a
"Place bid" button that opens ``tm-bid-modal``. The modal contains:

    input[name='bidAmount']                 – the bid amount (currency)
    input[name='autobid']                   – native auto-bid (max bid) switch
    input[name^='selectedShippingId-…']     – REQUIRED shipping radios
    input[name='emailIfOutbid']             – outbid email reminder
    button[type='submit']  ("Place bid")    – submit

We fill these, submit, optionally confirm, then re-read listing state to verify
the bid took effect.
"""
from __future__ import annotations

import time
from decimal import Decimal

from playwright.async_api import Page, TimeoutError as PWTimeout

from .. import config, db
from ..models import BidResult
from ..money import D, fmt
from .browser import browser
from . import listing as listing_mod

MODAL = "tm-bid-modal, tg-modal, .o-modal__dialog"
PLACE_BID_BUTTON = "tm-listing-auction button:has-text('Place bid')"

ERROR_KEYWORDS = (
    "too low", "must be at least", "higher than", "select a shipping",
    "choose a shipping", "already the highest", "cannot bid", "error",
    "not allowed", "invalid",
)


async def _shot(page: Page, name: str) -> None:
    try:
        path = config.SCREENSHOT_DIR / f"{name}-{int(time.time())}.png"
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass


async def _check_radio(modal, radio) -> bool:
    """Check a (visually hidden) shipping radio, clicking its label if needed."""
    try:
        await radio.check(force=True)
        return True
    except Exception:
        rid = await radio.get_attribute("id")
        if rid:
            try:
                await modal.locator(f"label[for='{rid}']").click()
                return True
            except Exception:
                return False
    return False


async def _select_shipping(modal, radios, n_ship, shipping_index, shipping_method):
    # 1) Try to match the exact option by its visible label (method or "Pick-up").
    if shipping_method:
        want = shipping_method.strip().lower()
        for i in range(n_ship):
            rid = await radios.nth(i).get_attribute("id")
            if not rid:
                continue
            try:
                txt = (await modal.locator(f"label[for='{rid}']")
                       .inner_text()).strip().lower()
            except Exception:
                txt = ""
            if want and want in txt:
                if await _check_radio(modal, radios.nth(i)):
                    return True
    # 2) Fall back to the resolved index – but only if we have one. For a
    #    label-only choice (e.g. pick-up) we must NOT silently pick a paid option.
    if shipping_index is None:
        return False
    idx = max(0, min(shipping_index, n_ship - 1))
    return await _check_radio(modal, radios.nth(idx))


async def place_bid(
    page: Page,
    url: str,
    *,
    amount: Decimal,
    autobid: bool,
    shipping_index: int | None,
    shipping_method: str | None = None,
    email_if_outbid: bool,
    listing_id: str | None = None,
) -> BidResult:
    """Place a single bid. ``page`` should already be on (or will navigate to)
    the listing. Returns a :class:`BidResult` reflecting verified outcome."""
    amount = D(amount)
    try:
        if not page.url.startswith(url.split("?")[0]):
            await page.goto(url, wait_until="domcontentloaded")

        # Wait for the SPA to hydrate the auction box.
        try:
            await page.locator(PLACE_BID_BUTTON).first.wait_for(
                state="visible", timeout=15_000
            )
        except PWTimeout:
            return BidResult(
                ok=False,
                message="Bidding box / 'Place bid' button not found "
                        "(listing may be closed).",
            )

        await page.locator(PLACE_BID_BUTTON).first.click()

        modal = page.locator(MODAL).first
        await modal.locator("input[name='bidAmount']").wait_for(
            state="visible", timeout=10_000
        )

        # 1) amount
        amount_input = modal.locator("input[name='bidAmount']").first
        await amount_input.fill("")
        await amount_input.fill(f"{amount:.2f}")

        # 2) shipping / pick-up (required when present). Prefer matching the
        # exact option the user picked by its label; fall back to index for
        # numbered shipping options.
        if shipping_index is None and shipping_method:
            # Label-only choice (e.g. pick-up): search the whole chooser, which
            # includes the pick-up radio that isn't in shippingOptions.
            group = modal.locator(
                "tm-choose-shipping input[type='radio'], "
                "input[name^='selectedShippingId-']"
            )
            gcount = await group.count()
            ok = (
                await _select_shipping(modal, group, gcount, None, shipping_method)
                if gcount else False
            )
            if not ok:
                await _shot(page, "delivery-option-missing")
                return BidResult(
                    ok=False,
                    message=f"Delivery option '{shipping_method}' was not found in the bid form.",
                    amount=amount,
                    autobid=autobid,
                    submitted=False,
                )
        else:
            radios = modal.locator("input[name^='selectedShippingId-']")
            n_ship = await radios.count()
            if shipping_index is not None:
                ok = (
                    await _select_shipping(modal, radios, n_ship,
                                           shipping_index, shipping_method)
                    if n_ship else False
                )
                if not ok:
                    await _shot(page, "delivery-option-missing")
                    return BidResult(
                        ok=False,
                        message="Selected shipping option was not found in the bid form.",
                        amount=amount,
                        autobid=autobid,
                        submitted=False,
                    )

        # 3) autobid switch
        autobid_input = modal.locator("input[name='autobid']")
        if await autobid_input.count():
            if autobid:
                await autobid_input.first.check(force=True)
            else:
                await autobid_input.first.uncheck(force=True)

        # 4) outbid email reminder
        outbid_input = modal.locator("input[name='emailIfOutbid']")
        if await outbid_input.count():
            if email_if_outbid:
                await outbid_input.first.check(force=True)
            else:
                await outbid_input.first.uncheck(force=True)

        # 5) R18 confirmation, if the listing demands it
        over18 = modal.locator(".tm-bid-modal__over-18 input, input[name='over18']")
        if await over18.count():
            try:
                await over18.first.check(force=True)
            except Exception:
                pass

        # 6) submit
        submit = modal.locator("button[type='submit']").first
        await submit.click()

        # Some flows show a confirm step – click it if it appears quickly.
        await _maybe_confirm(page)

        # Look for an inline validation/error message.
        await page.wait_for_timeout(1500)
        err = await _read_error(page)
        if err:
            await _shot(page, "bid-rejected")
            return BidResult(
                ok=False, message=f"TradeMe rejected the bid: {err}",
                amount=amount, autobid=autobid, submitted=False,
            )

        # The submit went through without an explicit rejection.
        # Verify by re-reading authoritative state.
        verified, detail = await _verify(url, listing_id, amount)
        if verified:
            db.log(None, "info", f"Bid placed {fmt(amount)} "
                                 f"({'autobid' if autobid else 'normal'}) – {detail}")
            return BidResult(
                ok=True, message=detail, amount=amount, autobid=autobid,
                submitted=True,
            )

        await _shot(page, "bid-unverified")
        return BidResult(
            ok=False, message=f"Bid submitted but could not verify ({detail}).",
            amount=amount, autobid=autobid, submitted=True,
        )

    except Exception as exc:  # noqa: BLE001
        await _shot(page, "bid-exception")
        return BidResult(
            ok=False, message=f"Exception while bidding: {exc}",
            amount=amount, autobid=autobid, submitted=False,
        )


async def _maybe_confirm(page: Page) -> None:
    for name in ("Confirm", "Confirm bid", "Yes, place bid", "Place bid"):
        try:
            btn = page.get_by_role("button", name=name)
            if await btn.count() and await btn.first.is_visible(timeout=1200):
                # Avoid re-clicking the original opener if the modal already closed.
                if await page.locator(MODAL).first.is_visible(timeout=500):
                    await btn.first.click()
                    await page.wait_for_timeout(800)
                    return
        except Exception:
            continue


async def _read_error(page: Page) -> str | None:
    selectors = [
        ".o-validation-summary", "tg-validation-summary",
        ".o-input__footer .o-input-footer__counter--invalid",
        "[class*='error']",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count():
                text = (await loc.first.inner_text()).strip()
                low = text.lower()
                if text and any(k in low for k in ERROR_KEYWORDS):
                    return text[:200]
        except Exception:
            continue
    return None


async def _verify(url: str, listing_id: str | None, amount: Decimal):
    """Re-fetch listing state and confirm we are now leading / price moved."""
    html = await browser.fetch_html(url)
    if not html:
        return False, "could not re-fetch listing"
    state = listing_mod.parse_state(html, listing_id)
    if state is None:
        return False, "could not parse listing after bid"
    if state.is_leader:
        return True, f"now leading at {fmt(state.current_price)}"
    if state.current_price >= amount:
        # Price reached/exceeded our bid – an autobid above us absorbed it.
        return True, f"price now {fmt(state.current_price)} (outbid by autobid)"
    return False, f"still {fmt(state.current_price)}, leader unchanged"
