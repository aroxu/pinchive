"""Credential records: registration, liveness bookkeeping, removal.

The cookie file handling itself lives in :mod:`app.services.credential_service`
(pure filesystem + HTTP); this module owns the DB rows.
"""

from __future__ import annotations

from app.core.db import connection, transaction
from app.models import Credential, CredentialStatus
from app.services import credential_service as cookies
from app.services.board_service import now


def list_credentials() -> list[Credential]:
    with connection():
        return list(Credential.select().order_by(Credential.name))


def get_credential(cred_id: int) -> Credential | None:
    with connection():
        return Credential.get_or_none(Credential.id == cred_id)


def create_credential(name: str, cookies_text: str) -> Credential:
    """Register a session and immediately keep-alive it, so rotation starts now.

    A cookie payload we cannot parse must not leave a dangling row behind, so the
    record is rolled back before the error propagates.
    """
    with transaction():
        cred = Credential.create(name=(name or "").strip() or "unnamed")
    try:
        cookies.save_cookies_text(cred.id, cookies_text)
    except ValueError:
        with transaction():
            cred.delete_instance()
        raise
    refresh_credential(cred.id)
    return get_credential(cred.id) or cred


def refresh_credential(cred_id: int) -> cookies.ValidationResult:
    """Keep-alive: rotate the session cookie and record the verdict."""
    result = cookies.refresh_session(cookies.cookies_path(cred_id))
    record_check(cred_id, active=result.active, message=result.message)
    return result


def record_check(cred_id: int, *, active: bool, message: str | None) -> None:
    with transaction():
        cred = Credential.get_or_none(Credential.id == cred_id)
        if cred is None:
            return
        cred.status = (
            CredentialStatus.active.value if active else CredentialStatus.expired.value
        )
        cred.last_checked_at = now()
        cred.last_error = None if active else message
        cred.updated_at = now()
        cred.save()


def delete_credential(cred_id: int) -> bool:
    with transaction():
        cred = Credential.get_or_none(Credential.id == cred_id)
        if cred is None:
            return False
        path = cookies.cookies_path(cred_id)
        if path.exists():
            path.unlink(missing_ok=True)
        cred.delete_instance()
    return True


def all_credential_ids() -> list[int]:
    with connection():
        return [c.id for c in Credential.select(Credential.id)]
