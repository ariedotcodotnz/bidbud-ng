"""SQLModel-backed persistence layer.

Single-user, low write-volume tool, so a process-wide SQLAlchemy engine plus
short-lived sessions is enough. The public helpers return plain dictionaries to
keep the rest of the app and templates stable while the storage internals use
SQLModel and Alembic.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import case, delete, inspect
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.engine import Engine

from . import config
from .models import BidLog, Job, Setting

_LOCK = threading.RLock()
_ENGINE: Engine | None = None


# --------------------------------------------------------------------------- #
# Connection + schema
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_url() -> str:
    return f"sqlite:///{config.DB_PATH}"


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(config.BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.BASE_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _database_url())
    return cfg


def engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _ENGINE = create_engine(
            _database_url(),
            connect_args={"check_same_thread": False},
        )
        with _ENGINE.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    return _ENGINE


def reset_engine() -> None:
    """Dispose the process-wide engine; test fixtures call this after DB swaps."""
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None


def _run_migrations() -> None:
    """Upgrade empty DBs; stamp existing pre-Alembic DBs at the initial revision."""
    eng = engine()
    inspector = inspect(eng)
    has_version = inspector.has_table("alembic_version")
    has_legacy_schema = all(
        inspector.has_table(name) for name in ("settings", "jobs", "bid_log")
    )
    cfg = _alembic_config()
    if has_legacy_schema and not has_version:
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")


def init_db() -> None:
    with _LOCK:
        _run_migrations()
        # SQLModel stays as a final safety net for tests or manually-stamped DBs;
        # Alembic owns schema evolution.
        SQLModel.metadata.create_all(engine())
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
    with _LOCK, Session(engine()) as session:
        changed = False
        for key, value in DEFAULT_SETTINGS.items():
            if session.get(Setting, key) is None:
                session.add(Setting(key=key, value=value))
                changed = True
        if changed:
            session.commit()


def _to_dict(row) -> dict:
    return row.model_dump()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: str | None = None) -> str | None:
    with _LOCK, Session(engine()) as session:
        row = session.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    return row.value


def get_bool(key: str) -> bool:
    return (get_setting(key) or "0").strip().lower() in {"1", "true", "yes", "on"}


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get_setting(key) or default)
    except (TypeError, ValueError):
        return default


def all_settings() -> dict[str, str]:
    with _LOCK, Session(engine()) as session:
        rows = session.exec(select(Setting)).all()
    merged = dict(DEFAULT_SETTINGS)
    merged.update({r.key: (r.value or "") for r in rows})
    return merged


def set_setting(key: str, value: str) -> None:
    with _LOCK, Session(engine()) as session:
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
            session.add(row)
        session.commit()


def set_settings(values: dict[str, str]) -> None:
    with _LOCK, Session(engine()) as session:
        for key, value in values.items():
            row = session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value=value))
            else:
                row.value = value
                session.add(row)
        session.commit()


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
    job = Job(
        listing_id=listing_id,
        url=url,
        title=title,
        strategy=strategy,
        max_bid=max_bid,
        status="scheduled",
        end_date=end_date,
        current_price=current_price,
        options=json.dumps(options),
        created_at=now,
        updated_at=now,
    )
    with _LOCK, Session(engine()) as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        return int(job.id)


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    valid_fields = set(Job.model_fields)
    with _LOCK, Session(engine()) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        for key, value in fields.items():
            if key not in valid_fields or key == "id":
                raise ValueError(f"Unknown job field: {key}")
            setattr(job, key, value)
        session.add(job)
        session.commit()


def get_job(job_id: int) -> dict | None:
    with _LOCK, Session(engine()) as session:
        row = session.get(Job, job_id)
        return _to_dict(row) if row else None


def list_jobs(statuses: Iterable[str] | None = None) -> list[dict]:
    with _LOCK, Session(engine()) as session:
        stmt = select(Job)
        if statuses:
            stmt = stmt.where(Job.status.in_(tuple(statuses)))
        stmt = stmt.order_by(
            case((Job.status.in_(ACTIVE_STATUSES), 0), else_=1),
            Job.end_date.asc(),
        )
        rows = session.exec(stmt).all()
        return [_to_dict(r) for r in rows]


ACTIVE_STATUSES = ("scheduled", "active")


def active_jobs() -> list[dict]:
    return list_jobs(ACTIVE_STATUSES)


def delete_job(job_id: int) -> None:
    with _LOCK, Session(engine()) as session:
        session.exec(delete(Job).where(Job.id == job_id))
        session.exec(delete(BidLog).where(BidLog.job_id == job_id))
        session.commit()


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #
def log(job_id: int | None, level: str, message: str) -> None:
    with _LOCK, Session(engine()) as session:
        session.add(BidLog(job_id=job_id, ts=_now(), level=level, message=message))
        session.commit()


def job_logs(job_id: int, limit: int = 200) -> list[dict]:
    with _LOCK, Session(engine()) as session:
        rows = session.exec(
            select(BidLog)
            .where(BidLog.job_id == job_id)
            .order_by(BidLog.id.desc())
            .limit(limit)
        ).all()
        return [_to_dict(r) for r in rows]


def recent_logs(limit: int = 100) -> list[dict]:
    with _LOCK, Session(engine()) as session:
        rows = session.exec(
            select(BidLog)
            .order_by(BidLog.id.desc())
            .limit(limit)
        ).all()
        return [_to_dict(r) for r in rows]
