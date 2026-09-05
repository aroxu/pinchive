"""Backward-compatible arq worker entry point.

Early Pinchive Compose files started the worker with
``app.tasks.WorkerSettings``.  Keep that import path working so pulling a new
image repairs those installations without requiring an immediate Compose-file
migration.
"""

from app.worker import WorkerSettings

__all__ = ["WorkerSettings"]
