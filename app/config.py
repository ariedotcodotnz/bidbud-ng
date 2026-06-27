"""Runtime configuration and on-disk paths.

Most *user* settings (strategy defaults, credentials, toggles) live in the
SQLite ``settings`` table and are editable from the dashboard. The handful of
values here are process-level and read from the environment so they can be set
before the app boots.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
DB_PATH = DATA_DIR / "bidbud.sqlite3"
STORAGE_STATE_PATH = DATA_DIR / "storage_state.json"
SECRET_KEY_PATH = DATA_DIR / "secret.key"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


HOST = os.environ.get("BIDBUD_HOST", "0.0.0.0")
PORT = int(os.environ.get("BIDBUD_PORT", "8000"))
HEADLESS = _bool(os.environ.get("BIDBUD_HEADLESS"), True)
ENGINE_LEAD_SECONDS = int(os.environ.get("BIDBUD_ENGINE_LEAD_SECONDS", "180"))

UI_USER = os.environ.get("BIDBUD_UI_USER", "").strip()
UI_PASS = os.environ.get("BIDBUD_UI_PASS", "").strip()

TRADEME_BASE = "https://www.trademe.co.nz"
LOGIN_URL = f"{TRADEME_BASE}/a/login"

# A normal-looking desktop user agent. TradeMe's Sentry config drops events
# from HeadlessChrome, but we still avoid advertising it to reduce friction.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
