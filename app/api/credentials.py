"""Credential (Pinterest session cookie) endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.schemas import CredentialCreateIn, CredentialOut
from app.services import credential_store

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


@router.get("", response_model=list[CredentialOut])
def list_credentials():
    return [CredentialOut.of(c) for c in credential_store.list_credentials()]


@router.post("", response_model=CredentialOut, status_code=201)
def create_credential(payload: CredentialCreateIn):
    try:
        cred = credential_store.create_credential(payload.name, payload.cookies)
    except ValueError as exc:
        raise HTTPException(400, f"cookie parse error: {exc}") from exc
    return CredentialOut.of(cred)


@router.post("/{cred_id}/validate", response_model=CredentialOut)
def validate_credential(cred_id: int):
    """Manual check — also a keep-alive: rotates and persists the cookie."""
    if credential_store.get_credential(cred_id) is None:
        raise HTTPException(404, "credential not found")
    credential_store.refresh_credential(cred_id)
    cred = credential_store.get_credential(cred_id)
    if cred is None:
        raise HTTPException(404, "credential not found")
    return CredentialOut.of(cred)


@router.delete("/{cred_id}", status_code=204)
def delete_credential(cred_id: int):
    if not credential_store.delete_credential(cred_id):
        raise HTTPException(404, "credential not found")
