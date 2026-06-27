"""Entry point: load .env (if present) then start the web app."""
from __future__ import annotations

import os
from pathlib import Path


def _load_env() -> None:
    env = Path(__file__).resolve().parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

import uvicorn  # noqa: E402

from app import config  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=False)
