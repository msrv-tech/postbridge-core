# Миграции Alembic

История до **squash** доступна в git до коммита с baseline.

**Текущий корень:** одна ревизия `20260326_core_squash` в `versions/20260326_squash_core_greenfield.py` — полная схема Core (baseline + `bot_fsm_state` + `bridges` / `rss_feeds`, без удалённого `live_sync_failed_posts`).

Новые БД: `alembic upgrade head`. Greenfield: пересоздание БД предпочтительнее, чем стыковка старой `alembic_version` с новой линией.
