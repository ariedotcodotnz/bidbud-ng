import asyncio

import pytest

from app.trademe import auth
from app.trademe.auth import LoginManager


def test_selector_lists_present():
    assert auth.USERNAME_SELECTORS and auth.PASSWORD_SELECTORS
    assert auth.SUBMIT_SELECTORS and auth.CODE_SELECTORS
    assert auth.CODE_SUBMIT_SELECTORS
    # The username list must include a broad text-input fallback.
    assert any("text" in s or "input:not([type])" in s for s in auth.USERNAME_SELECTORS)


class TestStatus:
    def test_shape_and_busy_flag(self):
        lm = LoginManager()
        st = lm.status()
        assert set(st) >= {"state", "message", "member_id", "busy"}
        assert st["state"] == "idle"
        assert st["busy"] is False

    def test_busy_when_awaiting(self):
        lm = LoginManager(state="awaiting_2fa")
        assert lm.status()["busy"] is True

    def test_busy_when_starting(self):
        lm = LoginManager(state="starting")
        assert lm.status()["busy"] is True


class TestSubmitCode:
    async def test_resolves_pending_future(self):
        lm = LoginManager(state="awaiting_2fa")
        lm._code_future = asyncio.get_running_loop().create_future()
        await lm.submit_code("  123456 ")
        assert lm._code_future.result() == "123456"

    async def test_ignored_when_not_awaiting(self):
        lm = LoginManager()  # idle
        await lm.submit_code("123456")
        assert "Not waiting" in lm.message


class TestStart:
    async def test_refuses_when_already_in_progress(self):
        lm = LoginManager(state="starting")
        await lm.start("e@example.com", "pw")
        assert "already in progress" in lm.message
        # No background task should have been created.
        assert lm._task is None


class FakeLoc:
    def __init__(self, visible=False):
        self.visible = visible
        self.clicked = False
        self.pressed = False
        self.first = self

    async def is_visible(self):
        return self.visible

    async def click(self):
        self.clicked = True

    async def press(self, key):
        if key == "Enter":
            self.pressed = True


class FakeFrame:
    def __init__(self, locs):
        self.locs = locs

    def locator(self, sel):
        return self.locs.get(sel, FakeLoc(False))


class TestSubmitHelper:
    async def test_clicks_visible_submit(self):
        button = FakeLoc(True)
        target = FakeLoc(True)
        lm = LoginManager()

        await lm._submit(
            FakeFrame({"button": button}), ["button"], press_enter=True,
            enter_targets=[target], label="login",
        )

        assert button.clicked is True
        assert target.pressed is False

    async def test_enter_fallback_uses_given_target(self):
        target = FakeLoc(True)
        lm = LoginManager()

        await lm._submit(
            FakeFrame({}), ["button"], press_enter=True,
            enter_targets=[target], label="2FA code",
        )

        assert target.pressed is True

    async def test_raises_when_no_submit_path(self):
        lm = LoginManager()

        with pytest.raises(RuntimeError, match="Could not submit"):
            await lm._submit(
                FakeFrame({}), ["button"], press_enter=True,
                enter_targets=[FakeLoc(False)], label="2FA code",
            )
