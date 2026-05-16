"""Fetcher: посты Postbridge из канона Core (content_items source_type=postbridge)."""

from __future__ import annotations

import asyncio
from typing import Any

from postbridge.db import SESSION_LOCAL
from postbridge.domain.errors import ConfigurationError
from postbridge.domain.models import PostPayload


def _parse_source_channel(source_channel: str) -> str:
    s = source_channel.strip()
    if s.startswith("pb/"):
        return s[3:].strip()
    return s


def _list_posts_sync(tenant_id: str, limit: int) -> list[PostPayload]:
    from postbridge.services.markdown_plain import md_to_plain
    from postbridge.services.postbridge_workspace_content import (
        content_item_to_api_dict,
        list_postbridge_content_items,
    )

    session = SESSION_LOCAL()
    try:
        rows = list_postbridge_content_items(
            session,
            tenant_id=tenant_id,
            status="published",
            limit=limit,
            offset=0,
        )
        out: list[PostPayload] = []
        for row in rows:
            d = content_item_to_api_dict(row)
            md = d.get("content_md") or ""
            plain = d.get("content_plain")
            text = (plain or "").strip() if plain else md_to_plain(md)
            out.append(
                PostPayload(
                    source_post_id=str(d["id"]),
                    text=text,
                    media_url=d.get("media_url"),
                    media_urls=d.get("media_urls"),
                )
            )
        return out
    finally:
        session.close()


class PostbridgeFetcher:
    async def fetch_posts(
        self,
        source_channel: str,
        limit: int,
        credentials: Any = None,
        *,
        tenant_id: str | None = None,
    ) -> list[PostPayload]:
        _ = credentials
        workspace_id = _parse_source_channel(source_channel)
        if not workspace_id:
            raise ConfigurationError(
                "Postbridge source_channel must be workspace_id or pb/workspace_id"
            )
        if not tenant_id:
            raise ConfigurationError(
                "tenant_id is required for Postbridge fetch (Core content_items tenant scope)"
            )
        return await asyncio.to_thread(_list_posts_sync, tenant_id, limit)
