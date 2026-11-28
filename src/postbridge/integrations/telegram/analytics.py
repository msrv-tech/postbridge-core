import asyncio
from datetime import timedelta

from telethon import TelegramClient, errors, functions
from telethon.sessions import StringSession

from postbridge.config import get_settings
from postbridge.integrations.analytics import AnalyticsUnavailable, counts, observed, require
from postbridge.integrations.telegram.proxy_config import telethon_proxy_from_url


def read_many(client, credentials, ids, channel):
    require(credentials and credentials.session_string and credentials.api_id and credentials.api_hash,
            "permission_required")
    ids = [value for value in ids if value.isascii() and value.isdigit() and 0 < int(value) < 2**31]
    if not ids:
        return {}

    async def fetch():
        proxy = telethon_proxy_from_url(get_settings().telegram_proxy_url)
        # Connect only with the explicit channel grant. Never start an interactive login.
        telegram = TelegramClient(StringSession(credentials.session_string), int(credentials.api_id), credentials.api_hash,
                                 proxy=proxy, connection_retries=0, request_retries=0, flood_sleep_threshold=0)
        try:
            await telegram.connect()
            require(await telegram.is_user_authorized(), "permission_required")
            me = await telegram.get_me()
            require(me is not None and not me.bot, "permission_required")
            entity = channel.removeprefix("tg/")
            numeric = entity.lstrip("-").isdigit()
            try:
                peer = await telegram.get_input_entity(int(entity) if numeric else entity)
            except ValueError:
                if not numeric:
                    raise
                # String sessions do not carry a persistent access-hash cache.
                peer = None
                async for dialog in telegram.iter_dialogs(limit=100):
                    if dialog.is_channel and dialog.id == int(entity):
                        peer = dialog.input_entity
                        break
                require(peer is not None, "permission_required")
            return await telegram(functions.messages.GetMessagesViewsRequest(peer=peer, id=[int(value) for value in ids], increment=False))
        finally:
            await telegram.disconnect()

    async def bounded():
        return await asyncio.wait_for(fetch(), timeout=20)

    try:
        received = asyncio.run(bounded())
    except errors.FloodWaitError as error:
        raise AnalyticsUnavailable("rate_limited", retry_after=timedelta(seconds=min(error.seconds, 86400))) from None
    except (errors.UnauthorizedError, errors.ForbiddenError):
        raise AnalyticsUnavailable("permission_required") from None
    return {post_id: observed(counts({"views": value.views, "shares": value.forwards,
                                    "comments": getattr(getattr(value, "replies", None), "replies", None)},
                                   {"views": "views", "shares": "shares", "comments": "comments"}))
            for post_id, value in zip(ids, received.views, strict=True)}
