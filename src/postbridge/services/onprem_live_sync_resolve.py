"""Разрешение on-prem live-sync по мостам Core (bridges), без LIVE_SYNC_* env."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from postbridge.models.domain import BridgeOrm, ChannelOrm

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OnpremLiveSyncContext:
    tenant_id: str
    target_channel_external_id: str
    target_core_channel_id: str
    target_platform: str
    workspace_id: str


def resolve_onprem_live_sync_from_bridges(session: Session, chat_id: int) -> OnpremLiveSyncContext | None:
    """
    Ищет активный мост live_sync: Telegram-канал (external_id = str(chat_id)) → target.

    Возвращает None, если канала или моста нет.
    """
    sid = str(chat_id)
    sources = list(
        session.scalars(
            select(ChannelOrm).where(
                ChannelOrm.platform == "telegram",
                ChannelOrm.external_id == sid,
            )
        ).all()
    )
    if not sources:
        logger.debug("onprem live-sync: no Core channel telegram external_id=%s", sid)
        return None
    if len(sources) > 1:
        logger.warning(
            "onprem live-sync: multiple telegram channels for external_id=%s, using first id=%s",
            sid,
            sources[0].id,
        )
    src = sources[0]

    bridges = list(
        session.scalars(
            select(BridgeOrm).where(
                BridgeOrm.source_channel_id == src.id,
                BridgeOrm.status == "active",
                BridgeOrm.mode == "live_sync",
            )
        ).all()
    )
    if not bridges:
        logger.debug("onprem live-sync: no active live_sync bridge for source_channel_id=%s", src.id)
        return None
    if len(bridges) > 1:
        logger.warning(
            "onprem live-sync: multiple active live_sync bridges for telegram chat_id=%s, using bridge_id=%s",
            chat_id,
            bridges[0].id,
        )
    b = bridges[0]

    tgt = session.get(ChannelOrm, b.target_channel_id)
    if tgt is None:
        logger.warning("onprem live-sync: target channel row missing id=%s", b.target_channel_id)
        return None
    ext = (tgt.external_id or "").strip()
    if not ext:
        logger.warning("onprem live-sync: target channel %s has empty external_id", tgt.id)
        return None

    workspace_id = ""
    if isinstance(b.settings_json, dict):
        workspace_id = str(
            b.settings_json.get("saas_workspace_id") or b.settings_json.get("workspace_id") or ""
        ).strip()

    return OnpremLiveSyncContext(
        tenant_id=b.tenant_id,
        target_channel_external_id=ext,
        target_core_channel_id=tgt.id,
        target_platform=(tgt.platform or "max").strip() or "max",
        workspace_id=workspace_id,
    )
