"""The four BidBud bidding strategies, as pure decision functions.

Each ``decide_*`` takes the current :class:`ListingState`, a small mutable
``StrategyMemory`` (per-job working memory) and a ``StrategyConfig`` and returns
a :class:`BidDecision`. The engine in :mod:`app.engine` owns the timing loop and
executes the decisions; keeping the logic here pure makes it easy to reason
about (and unit test).

All strategies only *act* within the final ``activate_seconds`` (default 120s),
matching BidBud's "all strategies activate exactly two minutes before the
auction ends". Acting earlier is handled separately by the engine's
"bid early if single bid left" check.

Because TradeMe hides competitors' auto-bid maximums, an "auto-bid" is detected
behaviourally: place a minimum bid and see whether you're instantly outbid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .models import ListingState
from .money import D, ZERO, one_increment


@dataclass
class StrategyConfig:
    max_bid: Decimal
    activate_seconds: int = 120     # final window in which strategies act
    snipe_seconds: int = 8          # Slow / Adaptive end-game snipe window
    fast_lead_seconds: int = 120    # Fast: place native autobid at T-N


@dataclass
class StrategyMemory:
    placed_fast: bool = False
    placed_max: bool = False
    autobid_detected: bool | None = None   # None = unknown
    awaiting_probe_result: bool = False
    last_action_amount: Decimal = field(default_factory=lambda: ZERO)
    last_bid_count: int = 0


@dataclass
class BidDecision:
    action: str                 # "wait" | "place"
    reason: str = ""
    amount: Decimal = field(default_factory=lambda: ZERO)
    autobid: bool = False
    is_probe: bool = False      # Adaptive auto-bid detection probe
    is_max: bool = False        # bidding our full maximum (skip cents nudge)


WAIT = lambda reason: BidDecision("wait", reason)  # noqa: E731


def _cap(amount: Decimal, cfg: StrategyConfig) -> Decimal:
    return min(D(amount), D(cfg.max_bid))


def _can_afford(state: ListingState, cfg: StrategyConfig) -> bool:
    return D(state.min_next_bid) <= D(cfg.max_bid)


# --------------------------------------------------------------------------- #
# Fast
# --------------------------------------------------------------------------- #
def decide_fast(state, mem, cfg) -> BidDecision:
    if mem.placed_fast:
        return WAIT("fast: native autobid already lodged")
    if state.seconds_left() > cfg.fast_lead_seconds:
        return WAIT("fast: waiting for T-2min")
    if not _can_afford(state, cfg):
        return WAIT("fast: min next bid exceeds your maximum")
    return BidDecision(
        "place",
        reason=f"fast: lodging native autobid for your max",
        amount=_cap(cfg.max_bid, cfg),
        autobid=True,
        is_max=True,
    )


# --------------------------------------------------------------------------- #
# Slow
# --------------------------------------------------------------------------- #
def decide_slow(state, mem, cfg) -> BidDecision:
    if state.seconds_left() > cfg.activate_seconds:
        return WAIT("slow: not yet in the final 2 minutes")
    if state.is_leader:
        return WAIT("slow: already leading")
    if state.seconds_left() > cfg.snipe_seconds:
        return WAIT(f"slow: waiting for the last {cfg.snipe_seconds}s")
    if not _can_afford(state, cfg):
        return WAIT("slow: min next bid exceeds your maximum")
    return BidDecision(
        "place",
        reason="slow: placing the minimum bid in the closing seconds",
        amount=_cap(state.min_next_bid, cfg),
        autobid=False,
    )


# --------------------------------------------------------------------------- #
# Blocking
# --------------------------------------------------------------------------- #
def decide_blocking(state, mem, cfg) -> BidDecision:
    if state.seconds_left() > cfg.activate_seconds:
        return WAIT("blocking: not yet in the final 2 minutes")
    if state.is_leader:
        return WAIT("blocking: holding the lead with an active autobid")
    if not _can_afford(state, cfg):
        return WAIT("blocking: min next bid exceeds your maximum")
    inc = one_increment(state.current_price, state.min_next_bid)
    target = max(D(state.min_next_bid), D(state.current_price) + inc)
    return BidDecision(
        "place",
        reason="blocking: autobid one increment above the leader",
        amount=_cap(target, cfg),
        autobid=True,
        is_max=True,  # exact target, no cents nudge
    )


# --------------------------------------------------------------------------- #
# Adaptive
# --------------------------------------------------------------------------- #
def decide_adaptive(state, mem, cfg) -> BidDecision:
    if state.seconds_left() > cfg.activate_seconds:
        return WAIT("adaptive: not yet in the final 2 minutes")
    if state.is_leader:
        return WAIT("adaptive: leading")
    if not _can_afford(state, cfg):
        return WAIT("adaptive: min next bid exceeds your maximum")

    # Resolve a pending detection probe from the previous loop.
    if mem.awaiting_probe_result:
        mem.awaiting_probe_result = False
        mem.autobid_detected = not state.is_leader  # outbid instantly => autobid
        reason = ("adaptive: detected a competing autobid"
                  if mem.autobid_detected else
                  "adaptive: no competing autobid – manual opponents")
        # fall through to act on the detection result below

    # Detection not yet attempted: send a probe minimum bid.
    if mem.autobid_detected is None:
        return BidDecision(
            "place",
            reason="adaptive: probing for a competing autobid (min bid)",
            amount=_cap(state.min_next_bid, cfg),
            autobid=False,
            is_probe=True,
        )

    if mem.autobid_detected:
        # Up against an autobid: bid quickly to your max to beat it.
        if mem.placed_max:
            return WAIT("adaptive: maxed out against a higher autobid")
        return BidDecision(
            "place",
            reason="adaptive: bidding your max to break the autobid",
            amount=_cap(cfg.max_bid, cfg),
            autobid=True,
            is_max=True,
        )

    # No autobid: bore the manual bidders – hold back during a flurry, then
    # snipe minimum bids in the closing seconds.
    war = state.bid_count - mem.last_bid_count >= 2
    mem.last_bid_count = state.bid_count
    if war and state.seconds_left() > cfg.snipe_seconds:
        return WAIT("adaptive: holding back during a bidding flurry")
    if state.seconds_left() > cfg.snipe_seconds:
        return WAIT(f"adaptive: waiting for the last {cfg.snipe_seconds}s")
    return BidDecision(
        "place",
        reason="adaptive: closing-seconds minimum bid",
        amount=_cap(state.min_next_bid, cfg),
        autobid=False,
    )


DISPATCH = {
    "fast": decide_fast,
    "slow": decide_slow,
    "blocking": decide_blocking,
    "adaptive": decide_adaptive,
}


def decide(strategy: str, state, mem, cfg) -> BidDecision:
    fn = DISPATCH.get(strategy, decide_fast)
    return fn(state, mem, cfg)
