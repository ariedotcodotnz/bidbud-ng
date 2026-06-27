"""Supervisor: ensures exactly one engine coroutine runs per active job.

Implemented with APScheduler's AsyncIOScheduler firing a light supervision tick
every few seconds. The engine coroutines themselves own the fine-grained bid
timing; this just (re)spawns them and reaps finished ones.
"""
from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import db, engine

_TASKS: dict[int, asyncio.Task] = {}
_scheduler: AsyncIOScheduler | None = None


async def _supervise() -> None:
    # Reap finished tasks.
    for job_id in list(_TASKS):
        task = _TASKS[job_id]
        if task.done():
            exc = task.exception() if not task.cancelled() else None
            if exc:
                db.log(job_id, "error", f"Engine task error: {exc!r}")
            _TASKS.pop(job_id, None)

    # Spawn engines for any active job lacking one.
    for job in db.active_jobs():
        jid = job["id"]
        if jid not in _TASKS:
            _TASKS[jid] = asyncio.create_task(engine.run_job(jid))


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _supervise, "interval", seconds=10, id="supervise",
        max_instances=1, coalesce=True,
    )
    _scheduler.start()


async def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    for task in list(_TASKS.values()):
        task.cancel()
    if _TASKS:
        await asyncio.gather(*_TASKS.values(), return_exceptions=True)
    _TASKS.clear()


def cancel_job(job_id: int) -> None:
    task = _TASKS.pop(job_id, None)
    if task is not None:
        task.cancel()
