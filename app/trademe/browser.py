"""A long-lived Playwright browser + context for the whole app.

The same authenticated context is reused for:
  * cheap state polling via ``context.request.get`` (no rendering), and
  * driving the bid modal via a real ``Page``.

Login state is persisted to ``data/storage_state.json`` so a 2FA login is only
needed occasionally.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from playwright.async_api import (
    APIResponse,
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from .. import config

# Light anti-automation masking. This won't beat TradeMe's login bot-challenge
# (F5/Shape) on a flagged/datacenter IP, but it makes ordinary browsing/bidding
# with an imported session look more like a normal browser.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['en-NZ','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
"""


class BrowserManager:
    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._context is not None:
                return
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=config.HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            await self._make_context()

    async def _new_context(self, storage_state=None) -> BrowserContext:
        ctx = await self._browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-NZ",
            timezone_id="Pacific/Auckland",
            storage_state=storage_state,
        )
        ctx.set_default_timeout(30_000)
        await ctx.add_init_script(_STEALTH_JS)
        return ctx

    async def _make_context(self) -> None:
        storage = (
            str(config.STORAGE_STATE_PATH)
            if config.STORAGE_STATE_PATH.exists()
            else None
        )
        self._context = await self._new_context(storage_state=storage)

    async def reset_context(self) -> None:
        """Recreate the context (e.g. after a fresh login wrote storage_state)."""
        async with self._lock:
            if self._context is not None:
                await self._context.close()
            await self._make_context()

    async def stop(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.close()
                self._context = None
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("BrowserManager not started")
        return self._context

    async def new_page(self) -> Page:
        return await self.context.new_page()

    async def fetch_html(self, url: str) -> str | None:
        """Authenticated GET that returns HTML without rendering the SPA."""
        try:
            resp: APIResponse = await self.context.request.get(
                url, timeout=20_000
            )
        except Exception:
            return None
        if not resp.ok:
            return None
        return await resp.text()

    async def save_storage_state(self) -> None:
        await self.context.storage_state(path=str(config.STORAGE_STATE_PATH))

    async def _session_member_id_for_context(
        self, context: BrowserContext
    ) -> int | None:
        try:
            resp: APIResponse = await context.request.get(
                config.TRADEME_BASE + "/a/", timeout=20_000
            )
        except Exception:
            return None
        if not resp.ok:
            return None
        html = await resp.text()
        from .listing import extract_state_json  # local import to avoid cycle

        parsed = extract_state_json(html) or {}
        member = (
            parsed.get("NGRX_STATE", {})
            .get("currentMember", {})
            .get("item", {})
            or {}
        )
        return member.get("memberId")

    async def apply_storage_state(self, state: dict) -> int | None:
        """Validate and persist an imported Playwright storage-state.

        Returns the logged-in member id if the imported session is valid. Invalid
        imports are rejected without replacing the currently saved session.
        """
        async with self._lock:
            if self._browser is None:
                raise RuntimeError("BrowserManager not started")
            probe = await self._new_context(storage_state=state)
            try:
                member_id = await self._session_member_id_for_context(probe)
            finally:
                await probe.close()
            if not member_id:
                return None

            config.STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = config.STORAGE_STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            tmp.replace(config.STORAGE_STATE_PATH)
            if self._context is not None:
                await self._context.close()
            await self._make_context()
            return member_id

    async def session_member_id(self) -> int | None:
        """Return the logged-in member id, or None if the session is invalid."""
        return await self._session_member_id_for_context(self.context)


# Singleton used across the app.
browser = BrowserManager()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
