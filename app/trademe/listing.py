"""Parse TradeMe listing state.

Instead of scraping the rendered DOM (brittle), we read the server-rendered
Angular state that TradeMe embeds in every page:

    <script id="frend-state" type="application/json">{ ...NGRX_STATE... }</script>

That blob contains the authoritative listing data (price, end time, minimum
next bid, reserve status, bid history) plus the logged-in member id, which is
exactly what the bidding engine needs.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..models import ListingState, ShippingOption
from ..money import D

_LISTING_ID_RE = re.compile(r"/listing/(\d+)")
_STATE_RE = re.compile(
    r'<script id="frend-state" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def listing_id_from_url(url: str) -> str | None:
    m = _LISTING_ID_RE.search(url)
    return m.group(1) if m else None


def normalise_url(url_or_id: str) -> str:
    """Accept either a full listing URL or a bare numeric id."""
    url_or_id = url_or_id.strip()
    if url_or_id.isdigit():
        return f"https://www.trademe.co.nz/a/marketplace/listing/{url_or_id}"
    return url_or_id


def _parse_tm_date(value) -> datetime | None:
    """TradeMe encodes dates as ``"__date__:2026-06-30T08:00:00.000Z"``."""
    if not value or not isinstance(value, str):
        return None
    raw = value.replace("__date__:", "").strip()
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_state_json(html: str) -> dict | None:
    m = _STATE_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _find_item(ngrx: dict, listing_id: str | None) -> dict | None:
    entities = (
        ngrx.get("listing", {})
        .get("cachedDetails", {})
        .get("entities", {})
    )
    if not entities:
        return None
    if listing_id and listing_id in entities:
        return entities[listing_id].get("item")
    # Fall back to the first cached listing.
    first = next(iter(entities.values()), None)
    return first.get("item") if first else None


def parse_state(html: str, listing_id: str | None = None) -> ListingState | None:
    state = extract_state_json(html)
    if not state:
        return None
    ngrx = state.get("NGRX_STATE", {})

    item = _find_item(ngrx, listing_id)
    if not item:
        return None

    member = ngrx.get("currentMember", {}).get("item", {}) or {}
    my_member_id = member.get("memberId")

    bids = item.get("bids", {}) or {}
    bid_list = bids.get("list", []) or []
    bid_count = item.get("bidCount", bids.get("totalCount", 0)) or 0
    leading_bidder_id = None
    current_price = D(item.get("startPrice"))
    if bid_list:
        top = bid_list[0]
        leading_bidder_id = (top.get("bidder") or {}).get("memberId")
        current_price = D(top.get("bidAmount"))
    elif item.get("maxBidAmount"):
        current_price = D(item.get("maxBidAmount"))

    shipping = [
        ShippingOption(
            shipping_id=str(s.get("shippingId")),
            method=s.get("method", ""),
            price=D(s.get("price")),
        )
        for s in (item.get("shippingOptions") or [])
        if s.get("shippingId") is not None
    ]

    end_date = _parse_tm_date(item.get("endDate")) or datetime.now(timezone.utc)

    return ListingState(
        listing_id=str(item.get("listingId") or listing_id or ""),
        title=item.get("title", ""),
        end_date=end_date,
        current_price=current_price,
        min_next_bid=D(item.get("minimumNextBidAmount") or current_price),
        start_price=D(item.get("startPrice")),
        bid_count=int(bid_count),
        reserve_met=bool(item.get("isReserveMet")),
        reserve_state=int(item.get("reserveState") or 0),
        leading_bidder_id=leading_bidder_id,
        my_member_id=my_member_id,
        shipping_options=shipping,
        allows_pickups=item.get("allowsPickups") not in (None, 1),
        has_ping=bool(item.get("hasPing")),
        raw=item,
    )
