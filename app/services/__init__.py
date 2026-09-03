"""Business logic.

Services own all reads/writes against the Peewee models and all filesystem
work. They never import from ``app.api`` and never touch HTTP types, so the
API layer stays a thin translation of HTTP <-> service calls, and the arq
worker can call exactly the same functions.
"""
