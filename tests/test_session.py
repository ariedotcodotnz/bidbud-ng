import json

import pytest

from app import config
from app.trademe.browser import BrowserManager
from app.trademe.session import parse_session_blob
from .factories import frend_html


class TestStorageStateInput:
    def test_playwright_storage_state(self):
        blob = json.dumps({
            "cookies": [
                {"name": "idsrv", "value": "abc", "domain": "www.trademe.co.nz",
                 "path": "/", "secure": True, "sameSite": "Lax"},
            ],
            "origins": [],
        })
        state = parse_session_blob(blob)
        assert state["origins"] == []
        assert len(state["cookies"]) == 1
        c = state["cookies"][0]
        assert c["name"] == "idsrv" and "trademe" in c["domain"]
        assert c["sameSite"] == "Lax"


class TestCookieListInput:
    def test_extension_export(self):
        blob = json.dumps([
            {"name": "a", "value": "1", "domain": ".trademe.co.nz",
             "expirationDate": 1790000000, "sameSite": "no_restriction"},
            {"name": "junk", "value": "x", "domain": "google.com"},  # filtered out
        ])
        state = parse_session_blob(blob)
        names = [c["name"] for c in state["cookies"]]
        assert names == ["a"]
        assert state["cookies"][0]["expires"] == 1790000000
        assert state["cookies"][0]["sameSite"] == "None"  # no_restriction -> None


class TestRawHeaderInput:
    def test_cookie_header(self):
        state = parse_session_blob("idsrv=abc; TMSADUID=xyz")
        names = {c["name"] for c in state["cookies"]}
        assert names == {"idsrv", "TMSADUID"}
        assert all(".trademe.co.nz" in c["domain"] for c in state["cookies"])

    def test_with_cookie_prefix(self):
        state = parse_session_blob("Cookie: idsrv=abc; foo=bar")
        assert {c["name"] for c in state["cookies"]} == {"idsrv", "foo"}


class TestErrors:
    def test_empty(self):
        with pytest.raises(ValueError, match="No session data"):
            parse_session_blob("")

    def test_no_trademe_cookies(self):
        blob = json.dumps([{"name": "a", "value": "1", "domain": "example.com"}])
        with pytest.raises(ValueError, match="No trademe.co.nz cookies"):
            parse_session_blob(blob)

    def test_garbage(self):
        with pytest.raises(ValueError):
            parse_session_blob("###not a cookie###")


class _Resp:
    ok = True

    def __init__(self, html):
        self._html = html

    async def text(self):
        return self._html


class _Req:
    def __init__(self, html):
        self._html = html

    async def get(self, *a, **k):
        return _Resp(self._html)


class _Ctx:
    def __init__(self, html):
        self.request = _Req(html)
        self.closed = False

    def set_default_timeout(self, timeout):
        self.timeout = timeout

    async def add_init_script(self, script):
        self.script = script

    async def close(self):
        self.closed = True


class _Browser:
    def __init__(self):
        self.storage_states = []

    async def new_context(self, **kwargs):
        state = kwargs.get("storage_state")
        self.storage_states.append(state)
        if isinstance(state, dict) and state.get("valid"):
            return _Ctx(frend_html(member_id=4242))
        return _Ctx(frend_html(member_id=None))


class TestApplyStorageState:
    async def test_invalid_import_does_not_overwrite_existing_session(self, tmp_path, monkeypatch):
        path = tmp_path / "storage_state.json"
        path.write_text("old-state", encoding="utf-8")
        monkeypatch.setattr(config, "STORAGE_STATE_PATH", path)

        bm = BrowserManager()
        bm._browser = _Browser()
        current = _Ctx(frend_html(member_id=999))
        bm._context = current

        assert await bm.apply_storage_state({"valid": False}) is None
        assert path.read_text(encoding="utf-8") == "old-state"
        assert current.closed is False

    async def test_valid_import_persists_and_reloads_context(self, tmp_path, monkeypatch):
        path = tmp_path / "storage_state.json"
        monkeypatch.setattr(config, "STORAGE_STATE_PATH", path)

        bm = BrowserManager()
        fake_browser = _Browser()
        bm._browser = fake_browser
        current = _Ctx(frend_html(member_id=999))
        bm._context = current

        assert await bm.apply_storage_state({"valid": True, "cookies": []}) == 4242
        assert json.loads(path.read_text(encoding="utf-8"))["valid"] is True
        assert current.closed is True
        assert fake_browser.storage_states[-1] == str(path)
