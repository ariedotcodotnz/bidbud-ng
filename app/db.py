"""Thin SQLite persistence layer.

Single-user, low write-volume tool, so a single shared connection guarded by a
lock is plenty. All access goes through small helper functions.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None


# --------------------------------------------------------------------------- #
# Connection + schema
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _CONN = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.execute("PRAGMA journal_mode=WAL")
    return _CONN


def init_db() -> None:
    with _LOCK:
        conn = connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id    TEXT NOT NULL,
                url           TEXT NOT NULL,
                title         TEXT,
                strategy      TEXT NOT NULL,
                max_bid       TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'scheduled',
                end_date      TEXT,
                current_price TEXT,
                min_next_bid  TEXT,
                bid_count     INTEGER DEFAULT 0,
                is_leader     INTEGER DEFAULT 0,
                reserve_met   INTEGER DEFAULT 0,
                last_action   TEXT,
                options       TEXT,           -- json snapshot of per-job options
                created_at    TEXT,
                updated_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS bid_log (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id  INTEGER,
                ts      TEXT,
                level   TEXT,
                message TEXT
            );
            """
        )
        conn.commit()
    _seed_default_settings()


DEFAULT_SETTINGS: dict[str, str] = {
    "trademe_email": "",
    "trademe_password_enc": "",
    "default_strategy": "fast",
    "enter_default_bid": "1",
    "bid_early_single_bid": "0",
    "dont_add_cents": "0",
    "email_if_outbid": "1",
    "shipping_preference": "cheapest",   # cheapest | dearest | none
    # Strategy timing (seconds)
    "snipe_seconds": "8",                # Slow: act within last N seconds
    "fast_lead_seconds": "120",          # Fast: place autobid at T-120s
    "poll_far_seconds": "5",
    "poll_near_seconds": "2",
    "poll_final_seconds": "1",
}


def _seed_default_settings() -> None:
    with _LOCK:
        conn = connect()
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: str | None = None) -> str | None:
    with _LOCK:
        row = connect().execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    return row["value"]


def get_bool(key: str) -> bool:
    return (get_setting(key) or "0").strip().lower() in {"1", "true", "yes", "on"}


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get_setting(key) or default)
    except (TypeError, ValueError):
        return default


def all_settings() -> dict[str, str]:
    with _LOCK:
        rows = connect().execute("SELECT key, value FROM settings").fetchall()
    merged = dict(DEFAULT_SETTINGS)
    merged.update({r["key"]: r["value"] for r in rows})
    return merged


def set_setting(key: str, value: str) -> None:
    with _LOCK:
        conn = connect()
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def set_settings(values: dict[str, str]) -> None:
    for key, value in values.items():
        set_setting(key, value)


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
def create_job(
    *,
    listing_id: str,
    url: str,
    title: str,
    strategy: str,
    max_bid: str,
    end_date: str | None,
    current_price: str | None,
    options: dict[str, Any],
) -> int:
    now = _now()
    with _LOCK:
        conn = connect()
        cur = conn.execute(
            """
            INSERT INTO jobs(listing_id, url, title, strategy, max_bid, status,
                             end_date, current_price, options, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?, ?)
            """,
            (
                listing_id, url, title, strategy, max_bid, end_date,
                current_price, json.dumps(options), now, now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with _LOCK:
        conn = connect()
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", values)
        conn.commit()


def get_job(job_id: int) -> dict | None:
    with _LOCK:
        row = connect().execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def list_jobs(statuses: Iterable[str] | None = None) -> list[dict]:
    with _LOCK:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = connect().execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) "
                "ORDER BY end_date ASC",
                tuple(statuses),
            ).fetchall()
        else:
            rows = connect().execute(
                "SELECT * FROM jobs ORDER BY "
                "CASE WHEN status IN ('scheduled','active') THEN 0 ELSE 1 END, "
                "end_date ASC"
            ).fetchall()
    return [dict(r) for r in rows]


ACTIVE_STATUSES = ("scheduled", "active")


def active_jobs() -> list[dict]:
    return list_jobs(ACTIVE_STATUSES)


def delete_job(job_id: int) -> None:
    with _LOCK:
        conn = connect()
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.execute("DELETE FROM bid_log WHERE job_id = ?", (job_id,))
        conn.commit()


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #
def log(job_id: int | None, level: str, message: str) -> None:
    with _LOCK:
        conn = connect()
        conn.execute(
            "INSERT INTO bid_log(job_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (job_id, _now(), level, message),
        )
        conn.commit()


def job_logs(job_id: int, limit: int = 200) -> list[dict]:
    with _LOCK:
        rows = connect().execute(
            "SELECT * FROM bid_log WHERE job_id = ? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_logs(limit: int = 100) -> list[dict]:
    with _LOCK:
        rows = connect().execute(
            "SELECT * FROM bid_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
