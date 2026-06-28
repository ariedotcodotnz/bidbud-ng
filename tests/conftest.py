"""Shared pytest fixtures."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import config, db
from .factories import DummyPage


def _close_conn() -> None:
    db.reset_engine()


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Isolated SQLite database per test."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    _close_conn()
    db.init_db()
    yield db
    _close_conn()


@pytest.fixture
def api(temp_db, monkeypatch):
    """A FastAPI TestClient with the browser + scheduler stubbed out.

    Yields ``(client, fakes)`` where ``fakes.member_id`` controls the logged-in
    state and ``fakes.html`` controls what ``browser.fetch_html`` returns.
    """
    from app import scheduler, main
    from app.trademe.browser import browser as bm

    fakes = SimpleNamespace(member_id=None, html=None)

    async def _start():
        return None

    async def _stop():
        return None

    async def _session_member_id():
        return fakes.member_id

    async def _fetch_html(url, *a, **k):
        return fakes.html

    async def _new_page():
        return DummyPage()

    monkeypatch.setattr(bm, "start", _start)
    monkeypatch.setattr(bm, "stop", _stop)
    monkeypatch.setattr(bm, "session_member_id", _session_member_id)
    monkeypatch.setattr(bm, "fetch_html", _fetch_html)
    monkeypatch.setattr(bm, "new_page", _new_page)
    monkeypatch.setattr(scheduler, "start", lambda: None)
    monkeypatch.setattr(scheduler, "stop", _stop)

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client, fakes
