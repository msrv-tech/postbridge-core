from collections.abc import Generator
import sys
from datetime import datetime, UTC
from sqlalchemy import (
    String,
    DateTime,
    Integer,
    Boolean,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    inspect,
    JSON,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from postbridge.config import get_settings
from postbridge.domain.errors import ConfigurationError


class Base(DeclarativeBase):
    """Базовый класс для ORM-моделей SQLAlchemy."""


class BatchImportRunOrm(Base):
    """Tenant-scoped batch import run (замена legacy sync_jobs)."""

    __tablename__ = "batch_import_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_batch_import_runs_tenant_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_channel: Mapped[str] = mapped_column(String(256), nullable=False)
    target_channel: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_posts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_core_channel_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("channels.id", ondelete="RESTRICT"), nullable=True
    )
    target_core_channel_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("channels.id", ondelete="RESTRICT"), nullable=True
    )
    batch_import_dispatch_enqueued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class BatchImportEnqueuedPostOrm(Base):
    """Пост batch import run, для которого создан publication_target (идемпотентность retry)."""

    __tablename__ = "batch_import_enqueued_posts"
    __table_args__ = (
        UniqueConstraint(
            "batch_import_run_id",
            "source_post_id",
            name="uq_batch_import_enqueued_run_source",
        ),
        Index("ix_batch_import_enqueued_posts_run_id", "batch_import_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_import_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("batch_import_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_post_id: Mapped[str] = mapped_column(String(128), nullable=False)
    publication_target_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("publication_targets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class BatchImportFetchedPostOrm(Base):
    """Прочитанные посты для batch import run."""

    __tablename__ = "batch_import_fetched_posts"
    __table_args__ = (
        UniqueConstraint(
            "batch_import_run_id",
            "source_post_id",
            name="uq_batch_import_fetched_run_source",
        ),
        Index("ix_batch_import_fetched_posts_run_id", "batch_import_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_import_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("batch_import_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_post_id: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    media_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class PublishedPostOrm(Base):
    """ORM-модель глобального реестра опубликованных постов (дедупликация job vs live-sync)."""

    __tablename__ = "published_posts"
    __table_args__ = (
        UniqueConstraint(
            "source_channel",
            "source_post_id",
            "target_channel",
            name="uq_published_posts_source_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_channel: Mapped[str] = mapped_column(String(256), nullable=False)
    source_post_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_channel: Mapped[str] = mapped_column(String(256), nullable=False)
    max_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class RssFeedItemOrm(Base):
    """ORM-модель поста в RSS-ленте (публикация в RSS как target)."""

    __tablename__ = "rss_feed_items"
    __table_args__ = (
        UniqueConstraint("feed_id", "source_post_id", name="uq_rss_feed_items_feed_source"),
        Index("ix_rss_feed_items_feed_id", "feed_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_channel: Mapped[str] = mapped_column(String(256), nullable=False)
    source_post_id: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    media_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_urls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class BotFsmStateOrm(Base):
    """Состояние FSM cloud Telegram-бота (aiogram)."""

    __tablename__ = "bot_fsm_state"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class AiServiceIdempotencyOrm(Base):
    """Кэш ответов internal AI API для заголовка X-Idempotency-Key."""

    __tablename__ = "ai_service_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_ai_idempotency_tenant_key",
        ),
        Index("ix_ai_idempotency_tenant", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class StatusEventOutboxOrm(Base):
    """ORM-модель outbox для статусных событий (отправка в SaaS)."""

    __tablename__ = "status_event_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_status_event_outbox_event_id"),
        Index("ix_status_event_outbox_status_next_attempt", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_import_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("batch_import_runs.id", ondelete="CASCADE"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.5")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PublicationStatusEventOutboxOrm(Base):
    """Outbox для событий publication.target.status.changed (контракт SaaS v1.4)."""

    __tablename__ = "publication_status_event_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_publication_status_event_outbox_event_id"),
        Index("ix_pub_status_ev_ob_status_next", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_target_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("publication_targets.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


import postbridge.models.domain  # noqa: E402, F401 — регистрация ORM канонического домена


def create_engine_and_session() -> tuple:
    """Создаёт Engine и сессию БД (для миграций и CLI)."""
    settings = get_settings()
    # Без пула в тестах: иначе незакрытая Session держит коннект и блокирует DROP в другом.
    # «pytest» в sys.modules — на случай импорта db до выставления APP_ENV в conftest.
    engine_kw: dict = {"future": True}
    if settings.app_env == "test" or "pytest" in sys.modules:
        engine_kw["poolclass"] = NullPool
    engine = create_engine(settings.database_url, **engine_kw)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, session_local


ENGINE, SESSION_LOCAL = create_engine_and_session()


def init_db() -> None:
    """Инициализирует БД: создаёт таблицы при отсутствии."""
    settings = get_settings()
    if settings.app_env == "test":
        Base.metadata.create_all(bind=ENGINE)
        if ENGINE.dialect.name == "postgresql":
            with ENGINE.begin() as connection:
                vector_type_available = bool(connection.execute(text("SELECT to_regtype('vector') IS NOT NULL")).scalar())
                if vector_type_available:
                    inspector = inspect(connection)
                    embedding_columns = {column["name"] for column in inspector.get_columns("content_embeddings")}
                    if "vector_pg" not in embedding_columns:
                        connection.execute(text("ALTER TABLE content_embeddings ADD COLUMN vector_pg vector(1536)"))
                    connection.execute(
                        text(
                            """
                            CREATE INDEX IF NOT EXISTS ix_content_embeddings_vector_pg_ivfflat
                            ON content_embeddings
                            USING ivfflat (vector_pg vector_cosine_ops)
                            """
                        )
                    )
        return
    db_inspector = inspect(ENGINE)
    required_tables = {
        "alembic_version",
        "bot_fsm_state",
        "batch_import_runs",
        "batch_import_enqueued_posts",
        "batch_import_fetched_posts",
        "published_posts",
        "rss_feed_items",
        "rss_feeds",
        "bridges",
        "status_event_outbox",
        "publication_status_event_outbox",
        "tenants",
        "channels",
        "channel_credentials",
        "installation_secrets",
        "content_items",
        "render_variants",
        "publication_plans",
        "publication_targets",
        "ai_service_idempotency",
        "media_assets",
        "content_media_links",
        "llm_provider_configs",
        "channel_style_profiles",
        "agent_policies",
        "agent_tasks",
        "agent_runs",
        "agent_run_steps",
        "content_candidates",
        "content_source_fingerprints",
        "content_embeddings",
        "agent_memories",
        "agent_checkpoints",
        "review_queue_items",
    }
    existing_tables = set(db_inspector.get_table_names())
    missing = required_tables - existing_tables
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ConfigurationError(
            "Database schema is not initialized. Run `alembic upgrade head` before starting core.",
            details={"missing_tables": missing_list},
        )


def get_db_session() -> Generator:
    """Dependency: предоставляет сессию БД для запроса (с автоматическим закрытием)."""
    session = SESSION_LOCAL()
    try:
        yield session
    finally:
        session.close()
