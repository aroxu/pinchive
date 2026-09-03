"""Cross-cutting infrastructure: configuration, database, i18n, time helpers.

Nothing in ``app.core`` may import from ``app.services`` or ``app.api``; the
dependency direction is strictly core -> models -> services -> api.
"""
