"""Telegram adapter for botkit."""

from postbridge.botkit.interfaces import PlatformAdapter

from .backend import get_backend
from .handlers import build_router


class TelegramPlatformAdapter(PlatformAdapter):
    name = "telegram"

    def build_router(self, backend):
        return build_router(backend)


def get_platform_adapter() -> PlatformAdapter:
    return TelegramPlatformAdapter()
