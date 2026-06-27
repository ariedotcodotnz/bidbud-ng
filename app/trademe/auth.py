"""TradeMe login + 2FA, driven from the web UI.

The login form is served inside an iframe from ``auth.trademe.co.nz`` (and the
2FA step from ``mfa.trademe.co.nz``). We drive that iframe with Playwright.

Flow / state machine::

    idle ──start()──▶ starting ──(2FA needed)──▶ awaiting_2fa ──submit_code()──▶ success
                              └──(no 2FA)───────────────────────────────────▶ success
                              └──(failure anywhere)────────────────────────▶ error

While ``awaiting_2fa`` the login coroutine is parked on an asyncio.Future that
the dashboard resolves when you type the code.

Field finding is deliberately defensive: the auth/mfa iframes are cross-origin
and render asynchronously, and TradeMe occasionally tweaks them. We therefore
*wait* for the form to appear and locate fields broadly (the username is simply
the first visible text/email input; the password is the first password input).
On any failure we save a screenshot **and the iframe's HTML** to
``data/screenshots/`` so the real field names can be recovered.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from playwright.async_api import Frame, Page

from .. import config, db
from .browser import browser

# Broad, ordered candidate selectors. First *visible* match wins.
USERNAME_SELECTORS = [
    "input[type='email']",
    "input#Email", "input[name='Email']", "input[name='email']",
    "input[name*='mail' i]", "input[id*='mail' i]",
    "input[name*='user' i]", "input[id*='user' i]",
    "input[autocomplete='username']",
    "input[type='text']", "input:not([type])",
]
PASSWORD_SELECTORS = [
    "input[type='password']",
    "input[autocomplete='current-password']",
    "input#Password", "input[name='Password']", "input[name='password']",
]
SUBMIT_SELECTORS = [
    "button[type='submit']", "input[type='submit']",
    "button[name='button']",
    "button:has-text('Log in')", "button:has-text('Login')",
    "button:has-text('Sign in')", "button:has-text('Continue')",
    "button:has-text('Next')",
]
CODE_SELECTORS = [
    "input[autocomplete='one-time-code']", "input[name='Code']", "input#Code",
    "input[name='code']", "input[inputmode='numeric']", "input[type='tel']",
]
CODE_SUBMIT_SELECTORS = [
    "button[type='submit']", "button:has-text('Verify')",
    "button:has-text('Confirm')", "button:has-text('Continue')",
    "button:has-text('Submit')",
]

AUTH_HOSTS = ("auth.trademe.co.nz", "mfa.trademe.co.nz")


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

            await self._fill_username(frame, email)
            password_input = await self._fill_password(frame, password)
            await self._submit(
                frame, SUBMIT_SELECTORS, press_enter=True,
                enter_targets=[password_input], label="login",
            )

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
            await self._shot(page, "login-error")
            dump = await self._dump_frame(page, "login-frame")
            hint = f" (saved {dump} for inspection)" if dump else ""
            self.message = f"Login failed: {exc}{hint}"
            db.log(None, "error", self.message)
        finally:
            self._code_future = None
            self._page = None
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _finalise(self, page: Page) -> None:
        member_id = None
        for _ in range(20):
            await asyncio.sleep(1.5)
            member_id = await browser.session_member_id()
            if member_id:
                break
        if not member_id:
            raise RuntimeError(
                "Submitted credentials but no logged-in session was detected. "
                "The password may be wrong, or a field/selector needs adjusting."
            )
        await browser.save_storage_state()
        await browser.reset_context()
        self.member_id = member_id
        self.state = "success"
        self.message = f"Logged in (member {member_id}). Session saved."
        db.log(None, "info", self.message)

    # ------------------------------------------------------------------ #
    # Iframe / field helpers
    # ------------------------------------------------------------------ #
    async def _auth_frame(self, page: Page) -> Frame | None:
        """Return the auth/mfa iframe's content frame (waiting for it to load)."""
        for _ in range(40):  # up to ~20s
            handle = await page.query_selector(
                "iframe.tm-auth-service-login-iframe__iframe, "
                "iframe[src*='auth.trademe.co.nz'], iframe[src*='mfa.trademe.co.nz']"
            )
            if handle:
                frame = await handle.content_frame()
                if frame and frame.url and frame.url != "about:blank":
                    return frame
            for frame in page.frames:
                if any(h in frame.url for h in AUTH_HOSTS):
                    return frame
            await asyncio.sleep(0.5)
        return None

    async def _first_visible(self, frame: Frame, selectors, timeout: float):
        """Poll until one of ``selectors`` is visible in the frame, or give up."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for sel in selectors:
                loc = frame.locator(sel).first
                try:
                    if await loc.is_visible():
                        return loc
                except Exception:
                    continue
            await asyncio.sleep(0.4)
        return None

    async def _fill_username(self, frame: Frame, email: str) -> None:
        loc = await self._first_visible(frame, USERNAME_SELECTORS, timeout=25)
        if loc is None:
            raise RuntimeError("Could not find the email/username field.")
        await loc.fill(email)

    async def _fill_password(self, frame: Frame, password: str):
        loc = await self._first_visible(frame, PASSWORD_SELECTORS, timeout=6)
        if loc is None:
            # Possibly an email-first two-step form: submit, then wait for it.
            await self._submit(
                frame, SUBMIT_SELECTORS, press_enter=False,
                label="email step",
            )
            loc = await self._first_visible(frame, PASSWORD_SELECTORS, timeout=20)
        if loc is None:
            raise RuntimeError("Could not find the password field.")
        await loc.fill(password)
        return loc

    async def _submit(
        self, frame: Frame, selectors, *, press_enter: bool,
        enter_targets=(), label: str = "form",
    ) -> None:
        for sel in selectors:
            loc = frame.locator(sel).first
            try:
                if await loc.is_visible():
                    await loc.click()
                    return
            except Exception:
                continue
        if press_enter:
            for target in enter_targets or ():
                try:
                    if await target.is_visible():
                        await target.press("Enter")
                        return
                except Exception:
                    continue
        raise RuntimeError(f"Could not submit the {label}.")

    # ------------------------------------------------------------------ #
    # 2FA
    # ------------------------------------------------------------------ #
    async def _maybe_2fa(self, page: Page) -> bool:
        frame = await self._auth_frame(page)
        if frame is None:
            return False
        if "mfa.trademe.co.nz" in frame.url:
            return True
        loc = await self._first_visible(frame, CODE_SELECTORS, timeout=3)
        return loc is not None

    async def _submit_2fa(self, page: Page, code: str) -> None:
        frame = await self._auth_frame(page)
        if frame is None:
            raise RuntimeError("Lost the 2FA iframe before submitting the code.")
        if await self._first_visible(frame, CODE_SELECTORS, timeout=15) is None:
            raise RuntimeError("Could not find the 2FA code field.")

        for sel in CODE_SELECTORS:
            loc = frame.locator(sel)
            try:
                count = await loc.count()
            except Exception:
                count = 0
            if count == 1:
                code_input = loc.first
                await code_input.fill(code)
                await self._submit(
                    frame, CODE_SUBMIT_SELECTORS, press_enter=True,
                    enter_targets=[code_input], label="2FA code",
                )
                return
            if count > 1:  # per-digit OTP boxes
                for i, ch in enumerate(code):
                    if i < count:
                        await loc.nth(i).fill(ch)
                last_input = loc.nth(min(max(len(code) - 1, 0), count - 1))
                await self._submit(
                    frame, CODE_SUBMIT_SELECTORS, press_enter=True,
                    enter_targets=[last_input], label="2FA code",
                )
                return
        raise RuntimeError("Could not find the 2FA code field.")

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #
    async def _shot(self, page: Page | None, name: str) -> None:
        if page is None:
            return
        try:
            path = config.SCREENSHOT_DIR / f"{name}-{int(time.time())}.png"
            await page.screenshot(path=str(path), full_page=True)
            db.log(None, "info", f"Saved screenshot {path.name}")
        except Exception:
            pass

    async def _dump_frame(self, page: Page | None, name: str) -> str | None:
        if page is None:
            return None
        try:
            frame = await self._auth_frame(page)
            if frame is None:
                return None
            html = await frame.content()
            path = config.SCREENSHOT_DIR / f"{name}-{int(time.time())}.html"
            path.write_text(html, encoding="utf-8")
            db.log(None, "info", f"Saved auth-iframe HTML to {path.name}")
            return path.name
        except Exception:
            return None


login_manager = LoginManager()
