import asyncio

from app.trademe import auth
from app.trademe.auth import LoginManager


def test_selector_lists_present():
    assert auth.EMAIL_SELECTORS and auth.PASSWORD_SELECTORS
    assert auth.SUBMIT_SELECTORS and auth.CODE_SELECTORS
    assert auth.CODE_SUBMIT_SELECTORS


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
