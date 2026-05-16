"""Фикстуры для unified migration tests: канал MAX в Core для target_core_channel_id."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy.orm import Session

from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
from postbridge.models.domain import ChannelCredentialOrm, ChannelOrm, TenantOrm


def seed_max_destination_channel(
    session: Session,
    tenant_id: str,
    *,
    channel_id: str | None = None,
) -> str:
    """Создаёт tenant (если нет), канал MAX destination и активные креды для publication_target."""
    if session.get(TenantOrm, tenant_id) is None:
        session.add(TenantOrm(id=tenant_id, name="ut"))
        session.flush()
    ch = channel_id or str(uuid4())
    session.add(
        ChannelOrm(
            id=ch,
            tenant_id=tenant_id,
            platform="max",
            kind="destination",
            title="Max",
            external_id="max/t",
            status="connected",
        )
    )
    session.flush()
    max_secret = encrypt_credential_secret(
        json.dumps({"token": "x", "base_url": "http://max.test"}, ensure_ascii=True)
    )
    session.add(
        ChannelCredentialOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            channel_id=ch,
            auth_type="api_key",
            encrypted_secret=max_secret,
            meta_json=None,
            status="active",
        )
    )
    session.flush()
    return ch


def seed_telegram_source_channel(
    session: Session,
    tenant_id: str,
    *,
    channel_id: str | None = None,
) -> str:
    """Создаёт tenant (если нет), канал Telegram source и креды для fetch миграции."""
    if session.get(TenantOrm, tenant_id) is None:
        session.add(TenantOrm(id=tenant_id, name="ut"))
        session.flush()
    ch = channel_id or str(uuid4())
    session.add(
        ChannelOrm(
            id=ch,
            tenant_id=tenant_id,
            platform="telegram",
            kind="source",
            title="Telegram source",
            external_id="tg/src",
            status="connected",
        )
    )
    session.flush()
    tg_secret = encrypt_credential_secret(
        json.dumps(
            {
                "api_id": "12345",
                "api_hash": "abcdef",
                "session_string": "test-session",
            },
            ensure_ascii=True,
        )
    )
    session.add(
        ChannelCredentialOrm(
            id=str(uuid4()),
            tenant_id=tenant_id,
            channel_id=ch,
            auth_type="telegram",
            encrypted_secret=tg_secret,
            meta_json=None,
            status="active",
        )
    )
    session.flush()
    return ch
