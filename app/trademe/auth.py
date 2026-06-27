"""TradeMe login + 2FA, driven from the web UI.

The login form is served inside an iframe from ``auth.trademe.co.nz`` (and the
2FA step from ``mfa.trademe.co.nz``). We drive that iframe with Playwright.

Flow / state machine:

    idle ──start()──▶ starting ──(2FA needed)──▶ awaiting_2fa ──submit_code()──▶ success
                              └──(no 2FA)───────────────────────────────────▶ success
                              └──(failure anywhere)────────────────────────▶ error

While ``awaiting_2fa`` the login coroutine is parked on an asyncio.Future that
the dashboard resolves when you type the code.

NOTE: the exact field selectors inside the auth/mfa iframes are not visible in
the server-rendered HTML (they're cross-origin), so we try several common
selectors and fall back gracefully. On any failure a screenshot is written to
``data/screenshots`` so you can adjust selectors for your account if needed.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from playwright.async_api import Frame, Page, TimeoutError as PWTimeout

from .. import config, db
from .browser import browser

# Candidate selectors – first match wins.
EMAIL_SELECTORS = [
    "input#Email", "input[name='Email']", "input[type='email']",
    "input[name='email']", "input[autocomplete='username']",
]
PASSWORD_SELECTORS = [
    "input#Password", "input[name='Password']", "input[type='password']",
    "input[name='password']", "input[autocomplete='current-password']",
]
SUBMIT_SELECTORS = [
    "button[type='submit']", "input[type='submit']",
    "button[name='button']", "button:has-text('Log in')",
    "button:has-text('Login')", "button:has-text('Sign in')",
]
CODE_SELECTORS = [
    "input[name='Code']", "input#Code", "input[name='code']",
    "input[autocomplete='one-time-code']", "input[inputmode='numeric']",
    "input[type='tel']",
]
CODE_SUBMIT_SELECTORS = [
    "button[type='submit']", "button:has-text('Verify')",
    "button:has-text('Confirm')", "button:has-text('Continue')",
    "button:has-text('Submit')",
]


@dataclass
class LoginManager:
    state: str = "idle"          # idle | starting | awaiting_2fa | success | error
    message: str = ""
    member_id: int | None = None
    _task: asyncio.Task | None = None
    _code_future: asyncio.Future | None = field(default=None, repr=False)
    _page: Page | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ #
    # Public API used by the web layer
    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        return {
            "state": self.state,
            "message": self.message,
            "member_id": self.member_id,
            "busy": self.state in ("starting", "awaiting_2fa"),
        }

    async def start(self, email: str, password: str) -> None:
        if self.state in ("starting", "awaiting_2fa"):
            self.message = "A login is already in progress."
            return
        self.state = "starting"
        self.message = "Opening TradeMe login…"
        self.member_id = None
        self._task = asyncio.create_task(self._run(email, password))

    async def submit_code(self, code: str) -> None:
        if self.state != "awaiting_2fa" or self._code_future is None:
            self.message = "Not waiting for a 2FA code right now."
            return
        if not self._code_future.done():
            self._code_future.set_result(code.strip())

    # ------------------------------------------------------------------ #
    # Internal flow
    # ------------------------------------------------------------------ #
    async def _run(self, email: str, password: str) -> None:
        page = None
        try:
            page = await browser.new_page()
            self._page = page
            await page.goto(config.LOGIN_URL, wait_until="domcontentloaded")

            frame = await self._auth_frame(page)
            if frame is None:
                raise RuntimeError("Could not find the login iframe.")

            await self._fill_first(frame, EMAIL_SELECTORS, email, "email")
            await self._fill_first(frame, PASSWORD_SELECTORS, password, "password")
            await self._click_first(frame, SUBMIT_SELECTORS, "login submit")

            # Wait briefly for either success or a 2FA prompt.
            await asyncio.sleep(3)

            if await self._maybe_2fa(page):
                self.state = "awaiting_2fa"
                self.message = "Enter the 2FA code TradeMe just sent you."
                db.log(None, "info", "Login: awaiting 2FA code.")
                loop = asyncio.get_running_loop()
                self._code_future = loop.create_future()
                try:
                    code = await asyncio.wait_for(self._code_future, timeout=300)
                except asyncio.TimeoutError:
                    raise RuntimeError("Timed out waiting for the 2FA code.")
                await self._submit_2fa(page, code)

            await self._finalise(page)
        except Exception as exc:  # noqa: BLE001 – surface any failure to the UI
            self.state = "error"
            self.message = f"Login failed: {exc}"
            db.log(None, "error", self.message)
            await self._shot(page, "login-error")
        finally:
            self._code_future = None
            self._page = None
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _finalise(self, page: Page) -> None:
        # Give the OAuth callback time to land and set cookies.
        member_id = None
        for _ in range(20):
            await asyncio.sleep(1.5)
            member_id = await browser.session_member_id()
            if member_id:
                break
        if not member_id:
            raise RuntimeError(
                "Submitted credentials but no logged-in session was detected."
            )
        await browser.save_storage_state()
        await browser.reset_context()
        self.member_id = member_id
        self.state = "success"
        self.message = f"Logged in (member {member_id}). Session saved."
        db.log(None, "info", self.message)

    # ------------------------------------------------------------------ #
    # Iframe / selector helpers
    # ------------------------------------------------------------------ #
    async def _auth_frame(self, page: Page) -> Frame | None:
        """Return the auth/mfa iframe's content frame."""
        for _ in range(20):
            # Prefer the known iframe element; fall back to URL matching.
            handle = await page.query_selector(
                "iframe.tm-auth-service-login-iframe__iframe, "
                "iframe[src*='auth.trademe.co.nz'], iframe[src*='mfa.trademe.co.nz']"
            )
            if handle:
                frame = await handle.content_frame()
                if frame:
                    return frame
            for frame in page.frames:
                if any(h in frame.url for h in ("auth.trademe.co.nz",
                                                "mfa.trademe.co.nz")):
                    return frame
            await asyncio.sleep(0.5)
        return None

    async def _fill_first(self, frame: Frame, selectors, value: str, label: str):
        for sel in selectors:
            loc = frame.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=4000)
                await loc.fill(value)
                return
            except PWTimeout:
                continue
        raise RuntimeError(f"Could not find the {label} field.")

    async def _click_first(self, frame: Frame, selectors, label: str):
        for sel in selectors:
            loc = frame.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=4000)
                await loc.click()
                return
            except PWTimeout:
                continue
        raise RuntimeError(f"Could not find the {label} button.")

    async def _maybe_2fa(self, page: Page) -> bool:
        frame = await self._auth_frame(page)
        if frame is None:
            return False
        if "mfa.trademe.co.nz" in frame.url:
            return True
        for sel in CODE_SELECTORS:
            try:
                if await frame.locator(sel).first.is_visible(timeout=1500):
                    return True
            except Exception:
                continue
        return False

    async def _submit_2fa(self, page: Page, code: str) -> None:
        frame = await self._auth_frame(page)
        if frame is None:
            raise RuntimeError("Lost the 2FA iframe before submitting the code.")

        # Single field first.
        for sel in CODE_SELECTORS:
            loc = frame.locator(sel)
            try:
                count = await loc.count()
            except Exception:
                count = 0
            if count == 1:
                await loc.first.fill(code)
                await self._click_first(frame, CODE_SUBMIT_SELECTORS, "verify")
                return
            if count > 1:
                # Per-digit OTP boxes.
                for i, ch in enumerate(code):
                    if i < count:
                        await loc.nth(i).fill(ch)
                await self._click_first(frame, CODE_SUBMIT_SELECTORS, "verify")
                return
        raise RuntimeError("Could not find the 2FA code field.")

    async def _shot(self, page: Page | None, name: str) -> None:
        if page is None:
            return
        try:
            path = config.SCREENSHOT_DIR / f"{name}-{int(time.time())}.png"
            await page.screenshot(path=str(path), full_page=True)
            db.log(None, "info", f"Saved screenshot {path.name}")
        except Exception:
            pass


login_manager = LoginManager()
