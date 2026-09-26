"""Tek auth katmanı: X-API-Key başlığı veya `Authorization: Bearer <key>`."""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from .config import Settings


def make_auth_dependency(settings: Settings):
    keys = [k.encode() for k in settings.api_keys]

    async def require_api_key(request: Request) -> None:
        if not keys:  # auth kapalı (geliştirme)
            return
        supplied = request.headers.get("x-api-key")
        if not supplied:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        if not supplied or not any(secrets.compare_digest(supplied.encode(), k) for k in keys):
            raise HTTPException(401, "Geçersiz veya eksik API anahtarı", headers={"WWW-Authenticate": "ApiKey"})

    return require_api_key
