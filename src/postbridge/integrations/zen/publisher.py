"""Publisher для публикации в Дзен.

Официального API для публикации статей в Дзен нет.
Рекомендуемый способ: RSS-интеграция — канал подключает RSS-ленту к вашему сайту.
Этот publisher возвращает заглушку: сохраняет post_id для идемпотентности,
но фактическая публикация идёт через ваш RSS-фид, который Дзен забирает сам.
"""

from __future__ import annotations

from postbridge.api.schemas import ZenCredentials
from postbridge.config import Settings, get_settings
from postbridge.domain.errors import ConfigurationError, ExternalApiError
from postbridge.domain.models import PostPayload


class ZenPublisher:
    """Заглушка для публикации в Дзен.

    Дзен не предоставляет API для программной публикации статей.
    Используйте RSS-интеграцию: настройте канал Дзен на подписку
    на RSS-ленту вашего сайта. Контент будет забираться автоматически.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def publish_post(
        self,
        target_channel: str,
        payload: PostPayload,
        credentials: ZenCredentials | None = None,
    ) -> str | None:
        """Не поддерживается: Дзен не имеет API публикации.

        Для публикации в Дзен:
        1. Настройте RSS-ленту на вашем сайте
        2. Подключите её к каналу Дзен в настройках канала
        3. Публикуйте контент на сайте — Дзен заберёт его через RSS
        """
        raise ExternalApiError(
            code="EXTERNAL_API_ZEN_NO_PUBLISH_API",
            message=(
                "Zen (Дзен) does not provide a publish API. "
                "Use RSS integration: connect your site's RSS feed to your Zen channel. "
                "Zen will fetch content automatically from the feed."
            ),
            source="zen",
            retryable=False,
            details={
                "target_channel": target_channel,
                "hint": "Configure Zen channel to subscribe to your site RSS feed",
            },
        )
