"""Загрузка строки channel_credentials для канала и tenant."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from postbridge.models.domain import ChannelCredentialOrm

_CREDENTIAL_STATUS_ACTIVE = "active"


def load_channel_credential_row(
    session: Session, channel_id: str, tenant_id: str
) -> ChannelCredentialOrm | None:
    rows = list(
        session.scalars(
            select(ChannelCredentialOrm)
            .where(
                ChannelCredentialOrm.channel_id == channel_id,
                ChannelCredentialOrm.tenant_id == tenant_id,
            )
            .order_by(ChannelCredentialOrm.created_at)
        ).all()
    )
    if not rows:
        return None
    active = [r for r in rows if r.status == _CREDENTIAL_STATUS_ACTIVE]
    return active[0] if active else rows[0]
