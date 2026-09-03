"""Shared API dependencies: the job queue handle."""

from __future__ import annotations

from arq.connections import ArqRedis
from fastapi import Request


def get_queue(request: Request) -> ArqRedis | None:
    """The arq pool, or None when Redis is unreachable (UI degrades to read-only)."""
    return getattr(request.app.state, "arq", None)


async def enqueue(queue: ArqRedis | None, name: str, *args) -> bool:
    if queue is None:
        return False
    try:
        await queue.enqueue_job(name, *args)
        return True
    except Exception:  # noqa: BLE001 — a dead queue must not 500 the request
        return False
