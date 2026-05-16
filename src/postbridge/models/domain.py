"""Канонические сущности Core: tenant, каналы, контент, план, targets (architecture §3)."""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from postbridge.db import Base


class TenantOrm(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    image_style_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ChannelOrm(Base):
    __tablename__ = "channels"
    __table_args__ = (Index("ix_channels_tenant_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ChannelCredentialOrm(Base):
    __tablename__ = "channel_credentials"
    __table_args__ = (Index("ix_channel_credentials_tenant_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ContentItemOrm(Base):
    __tablename__ = "content_items"
    __table_args__ = (Index("ix_content_items_tenant_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_structured_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    media_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ContentItemAiChatMessageOrm(Base):
    """История чата редактора с ИИ по посту (internal / SaaS BFF)."""

    __tablename__ = "content_item_ai_chat_messages"
    __table_args__ = (
        Index("ix_ciacm_tenant_content", "tenant_id", "content_item_id"),
        Index("ix_ciacm_tenant_content_created", "tenant_id", "content_item_id", "created_at"),
        CheckConstraint("role IN ('user','assistant','system')", name="ck_ciacm_role"),
        CheckConstraint(
            "kind IN ('message','action','result','error')",
            name="ck_ciacm_kind",
        ),
        CheckConstraint(
            "status IS NULL OR status IN ('pending','running','done','failed')",
            name="ck_ciacm_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    content_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="message")
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class RenderVariantOrm(Base):
    __tablename__ = "render_variants"
    __table_args__ = (
        Index("ix_render_variants_tenant_id", "tenant_id"),
        Index("ix_render_variants_content_item_id", "content_item_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    content_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[str | None] = mapped_column(Text, nullable=True)
    mentions: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class PublicationPlanOrm(Base):
    __tablename__ = "publication_plans"
    __table_args__ = (
        Index("ix_publication_plans_tenant_id", "tenant_id"),
        Index("ix_publication_plans_content_item_id", "content_item_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    content_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PublicationTargetOrm(Base):
    __tablename__ = "publication_targets"
    __table_args__ = (
        Index("ix_publication_targets_tenant_id", "tenant_id"),
        Index("ix_publication_targets_publication_plan_id", "publication_plan_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    publication_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("publication_plans.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("channels.id", ondelete="RESTRICT"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    render_variant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("render_variants.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_post_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class MediaAssetOrm(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_media_assets_byte_size"),
        Index("ix_media_assets_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class MediaGenerationJobOrm(Base):
    __tablename__ = "media_generation_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_media_generation_jobs_status",
        ),
        CheckConstraint(
            "target IN ('cover', 'media')",
            name="ck_media_generation_jobs_target",
        ),
        Index("ix_media_generation_jobs_tenant_created", "tenant_id", "created_at"),
        Index("ix_media_generation_jobs_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    requester_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="SET NULL"), nullable=True
    )
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_tokens_charged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContentMediaLinkOrm(Base):
    __tablename__ = "content_media_links"
    __table_args__ = (
        UniqueConstraint(
            "content_item_id",
            "media_asset_id",
            "role",
            name="uq_content_media_links_item_asset_role",
        ),
        Index("ix_content_media_links_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    content_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="attachment")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class BridgeOrm(Base):
    """Связь source_channel → target_channel (live_sync, migration), tenant-scoped."""

    __tablename__ = "bridges"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'error')",
            name="ck_bridges_status",
        ),
        CheckConstraint(
            "mode IN ('live_sync', 'migration')",
            name="ck_bridges_mode",
        ),
        UniqueConstraint(
            "tenant_id",
            "saas_user_id",
            "source_channel_id",
            "target_channel_id",
            "mode",
            name="uq_bridges_tenant_user_src_tgt_mode",
        ),
        Index("ix_bridges_tenant_id", "tenant_id"),
        Index("ix_bridges_saas_user_id", "saas_user_id"),
        Index("ix_bridges_source_channel", "source_channel_id"),
        Index("ix_bridges_target_channel", "target_channel_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    saas_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_channel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    target_channel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="live_sync")
    settings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class RssFeedOrm(Base):
    """Метаданные RSS-ленты (источник = Core channel)."""

    __tablename__ = "rss_feeds"
    __table_args__ = (
        Index("ix_rss_feeds_tenant_id", "tenant_id"),
        Index("ix_rss_feeds_source_channel", "source_channel_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_channel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    secret_token: Mapped[str] = mapped_column(String(128), nullable=False)
    saas_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AgentTaskOrm(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint("mode IN ('post_copilot', 'topic_scout')", name="ck_agent_tasks_mode"),
        CheckConstraint("status IN ('active', 'paused', 'archived')", name="ck_agent_tasks_status"),
        Index("ix_agent_tasks_tenant_id", "tenant_id"),
        Index("ix_agent_tasks_channel_id", "channel_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    editorial_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_cron: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_candidates_per_run: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    autonomy_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="draft_approval")
    provider_config_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_provider_configs.id", ondelete="SET NULL"), nullable=True
    )
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="SET NULL"), nullable=True
    )
    task_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class AgentRunOrm(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'awaiting_review', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint("trigger_type IN ('manual', 'scheduled', 'retry', 'api')", name="ck_agent_runs_trigger_type"),
        Index("ix_agent_runs_tenant_id", "tenant_id"),
        Index("ix_agent_runs_task_id", "agent_task_id"),
        Index("ix_agent_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    agent_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    content_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="SET NULL"), nullable=True
    )
    graph_name: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="api")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    user_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class AgentRunStepOrm(Base):
    __tablename__ = "agent_run_steps"
    __table_args__ = (
        Index("ix_agent_run_steps_run_id", "agent_run_id"),
        Index("ix_agent_run_steps_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class ChannelStyleProfileOrm(Base):
    __tablename__ = "channel_style_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel_id", name="uq_style_profiles_tenant_channel"),
        Index("ix_style_profiles_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="hybrid")
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class ContentCandidateOrm(Base):
    __tablename__ = "content_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'approved', 'rejected', 'converted', 'superseded')",
            name="ck_content_candidates_status",
        ),
        Index("ix_content_candidates_tenant_id", "tenant_id"),
        Index("ix_content_candidates_run_id", "agent_run_id"),
        Index("ix_content_candidates_channel_id", "channel_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    content_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    topic: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_now: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_bundle_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    scores_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedup_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    style_fit_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class ContentSourceFingerprintOrm(Base):
    __tablename__ = "content_source_fingerprints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel_id", "source_url_hash", name="uq_source_fingerprint_tenant_channel_url"),
        Index("ix_source_fingerprints_tenant_channel", "tenant_id", "channel_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    source_url_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_title_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    semantic_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_content_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("content_items.id", ondelete="SET NULL"), nullable=True
    )
    candidate_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("content_candidates.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class ContentEmbeddingOrm(Base):
    __tablename__ = "content_embeddings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_type", "entity_id", name="uq_content_embeddings_entity"),
        Index("ix_content_embeddings_tenant_channel", "tenant_id", "channel_id"),
        Index("ix_content_embeddings_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    vector_json: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class AgentMemoryOrm(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        Index("ix_agent_memories_tenant_channel", "tenant_id", "channel_id"),
        Index("ix_agent_memories_memory_type", "memory_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    memory_json: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class LlmProviderConfigOrm(Base):
    __tablename__ = "llm_provider_configs"
    __table_args__ = (
        Index("ix_llm_provider_configs_tenant_id", "tenant_id"),
        Index("ix_llm_provider_configs_is_default", "is_default"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    capabilities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class AgentPolicyOrm(Base):
    __tablename__ = "agent_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel_id", name="uq_agent_policies_tenant_channel"),
        Index("ix_agent_policies_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=True)
    policy_json: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class AgentCheckpointOrm(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "checkpoint_key", name="uq_agent_checkpoints_run_key"),
        Index("ix_agent_checkpoints_run_id", "agent_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    checkpoint_key: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class ReviewQueueItemOrm(Base):
    __tablename__ = "review_queue_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'superseded')",
            name="ck_review_queue_items_status",
        ),
        Index("ix_review_queue_items_tenant_status", "tenant_id", "status"),
    )
    __mapper_args__ = {"confirm_deleted_rows": False}

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    agent_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), ForeignKey("content_candidates.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    review_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    decision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
