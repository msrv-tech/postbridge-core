from dataclasses import dataclass
import os

from postbridge.domain.errors import ConfigurationError


@dataclass(slots=True)
class Settings:
    app_env: str
    postbridge_app_mode: str
    postbridge_selfhost_tenant_id: str
    database_url: str
    redis_url: str
    celery_task_always_eager: bool
    telegram_api_id: str | None
    telegram_api_hash: str | None
    telegram_session_name: str
    telegram_session_string: str | None
    telegram_bot_token: str | None
    max_api_base_url: str | None
    max_api_token: str | None
    max_api_timeout_seconds: int
    vk_access_token: str | None
    vk_user_access_token: str | None
    linkedin_access_token: str | None
    linkedin_author_urn: str | None
    linkedin_api_version: str
    batch_import_run_max_retries: int
    batch_import_run_retry_delay_seconds: int
    batch_import_run_retry_backoff_multiplier: float
    batch_import_run_retry_max_delay_seconds: int
    batch_import_run_stuck_timeout_seconds: int
    status_event_webhook_url: str | None
    status_event_webhook_timeout_seconds: int
    status_event_webhook_token: str | None
    saas_base_url: str | None
    saas_bot_secret: str | None
    core_base_url: str
    sync_publish_token: str | None
    bot_mode: str
    bot_backend: str
    bot_webhook_path: str
    bot_webhook_secret: str | None
    bot_webhook_base_url: str | None
    web_app_base_url: str | None
    magic_link_base_url: str | None
    telegram_bot_username: str
    core_service_token: str | None
    status_event_outbox_batch_size: int
    status_event_outbox_max_retries: int
    status_event_outbox_retry_delay_seconds: int
    status_event_outbox_backoff_multiplier: float
    status_event_outbox_retry_max_delay_seconds: int
    telegram_proxy_url: str | None
    telegram_proxy_fallback_direct: bool
    ai_gateway_enabled: bool
    ai_gateway_base_url: str | None
    ai_gateway_api_key: str | None
    ai_gateway_timeout_seconds: int
    ai_gateway_default_model: str | None
    ai_gateway_default_response_language: str | None
    ai_image_generation_model: str | None
    ai_image_generation_size: str
    ai_image_style_prompt: str | None
    credentials_encryption_key: str | None
    sentry_dsn: str | None
    sentry_traces_sample_rate: float
    media_storage_type: str
    media_storage_path: str | None
    media_base_url: str | None
    s3_endpoint_url: str | None
    s3_region: str
    s3_bucket: str | None
    s3_access_key: str | None
    s3_secret_key: str | None
    s3_public_base_url: str | None
    postbridge_default_locale: str
    postbridge_default_timezone: str
    agent_search_backend: str
    agent_llm_base_url: str | None
    agent_llm_api_key: str | None
    agent_llm_default_model: str | None
    agent_llm_embedding_model: str | None
    agent_llm_max_tokens: int
    agent_default_response_language: str | None
    agent_search_backends: tuple[str, ...]
    agent_search_max_results: int
    agent_search_query_variants: int
    agent_search_fetch_budget: int
    agent_search_searxng_base_url: str | None
    agent_search_searxng_api_key: str | None
    agent_search_language: str | None
    agent_search_allowed_domains: tuple[str, ...]
    agent_search_blocked_domains: tuple[str, ...]
    agent_search_blocked_source_types: tuple[str, ...]
    agent_search_max_source_age_hours: int
    agent_cleanup_retention_days: int
    agent_trace_retention_days: int
    agent_review_retention_days: int
    agent_fingerprint_retention_days: int
    agent_vector_backend: str
    agent_vector_dimensions: int
    agent_embedding_drift_reindex_interval_seconds: int
    agent_embedding_drift_channel_limit: int
    agent_embedding_drift_item_limit: int
    agent_embedding_maintenance_interval_seconds: int
    agent_embedding_compaction_interval_seconds: int
    agent_embedding_candidate_retention_days: int
    agent_trace_policy: str
    agent_trace_max_entries: int
    agent_trace_compaction_mode: str
    agent_review_body_retention_days: int


def _to_bool(value: str | None, default: bool = False) -> bool:
    """Парсит строку в bool (1/true/yes/on)."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _strip_optional_env(value: str | None) -> str | None:
    """Пустая или пробельная строка → None."""
    if value is None:
        return None
    s = value.strip()
    return s if s else None


def _first_optional_env(*names: str) -> str | None:
    """Первое непустое значение среди имён переменных (как S3_* и MEDIA_S3_*)."""
    for name in names:
        v = _strip_optional_env(os.getenv(name))
        if v is not None:
            return v
    return None


def get_settings() -> Settings:
    """Возвращает настройки из переменных окружения."""
    return Settings(
        app_env=os.getenv("APP_ENV", "dev"),
        postbridge_app_mode=(
            os.getenv("POSTBRIDGE_APP_MODE", "selfhost").strip().lower() or "selfhost"
        ),
        postbridge_selfhost_tenant_id=(
            os.getenv("POSTBRIDGE_SELFHOST_TENANT_ID", "00000000-0000-4000-8000-000000000001").strip()
            or "00000000-0000-4000-8000-000000000001"
        ),
        database_url=(os.getenv("DATABASE_URL") or "").strip(),
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        celery_task_always_eager=_to_bool(os.getenv("CELERY_TASK_ALWAYS_EAGER"), False),
        telegram_api_id=os.getenv("TELEGRAM_API_ID"),
        telegram_api_hash=os.getenv("TELEGRAM_API_HASH"),
        telegram_session_name=os.getenv("TELEGRAM_SESSION_NAME", "postbridge"),
        telegram_session_string=os.getenv("TELEGRAM_SESSION_STRING"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        max_api_base_url=os.getenv("MAX_API_BASE_URL"),
        max_api_token=os.getenv("MAX_API_TOKEN"),
        max_api_timeout_seconds=int(os.getenv("MAX_API_TIMEOUT_SECONDS", "30")),
        vk_access_token=os.getenv("VK_ACCESS_TOKEN"),
        vk_user_access_token=os.getenv("VK_USER_ACCESS_TOKEN"),
        linkedin_access_token=_strip_optional_env(os.getenv("LINKEDIN_ACCESS_TOKEN")),
        linkedin_author_urn=_strip_optional_env(os.getenv("LINKEDIN_AUTHOR_URN")),
        linkedin_api_version=os.getenv("LINKEDIN_API_VERSION", "202601").strip()
        or "202601",
        batch_import_run_max_retries=int(os.getenv("BATCH_IMPORT_RUN_MAX_RETRIES", "2")),
        batch_import_run_retry_delay_seconds=int(
            os.getenv("BATCH_IMPORT_RUN_RETRY_DELAY_SECONDS", "10")
        ),
        batch_import_run_retry_backoff_multiplier=float(
            os.getenv("BATCH_IMPORT_RUN_RETRY_BACKOFF_MULTIPLIER", "2.0")
        ),
        batch_import_run_retry_max_delay_seconds=int(
            os.getenv("BATCH_IMPORT_RUN_RETRY_MAX_DELAY_SECONDS", "300")
        ),
        batch_import_run_stuck_timeout_seconds=int(
            os.getenv("BATCH_IMPORT_RUN_STUCK_TIMEOUT_SECONDS", "900")
        ),
        status_event_webhook_url=os.getenv("STATUS_EVENT_WEBHOOK_URL"),
        status_event_webhook_timeout_seconds=int(
            os.getenv("STATUS_EVENT_WEBHOOK_TIMEOUT_SECONDS", "5")
        ),
        status_event_webhook_token=_strip_optional_env(
            os.getenv("STATUS_EVENT_WEBHOOK_TOKEN") or os.getenv("CORE_EVENT_TOKEN")
        ),
        saas_base_url=_strip_optional_env(os.getenv("SAAS_BASE_URL")),
        saas_bot_secret=_strip_optional_env(os.getenv("BOT_SECRET")),
        core_base_url=os.getenv("CORE_BASE_URL", "http://127.0.0.1:8000").strip(),
        sync_publish_token=os.getenv("SYNC_PUBLISH_TOKEN"),
        bot_mode=os.getenv("BOT_MODE", "long_polling").strip().lower() or "long_polling",
        bot_backend=os.getenv("BOT_BACKEND", "saas").strip().lower() or "saas",
        bot_webhook_path=os.getenv("BOT_WEBHOOK_PATH", "/internal/bot/webhook").strip()
        or "/internal/bot/webhook",
        bot_webhook_secret=_strip_optional_env(os.getenv("BOT_WEBHOOK_SECRET")),
        bot_webhook_base_url=_strip_optional_env(os.getenv("BOT_WEBHOOK_BASE_URL")),
        web_app_base_url=_strip_optional_env(os.getenv("WEB_APP_BASE_URL")),
        magic_link_base_url=_strip_optional_env(os.getenv("MAGIC_LINK_BASE_URL")),
        telegram_bot_username=(os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@"),
        core_service_token=_strip_optional_env(os.getenv("CORE_SERVICE_TOKEN")),
        status_event_outbox_batch_size=int(
            os.getenv("STATUS_EVENT_OUTBOX_BATCH_SIZE", "100")
        ),
        status_event_outbox_max_retries=int(
            os.getenv("STATUS_EVENT_OUTBOX_MAX_RETRIES", "10")
        ),
        status_event_outbox_retry_delay_seconds=int(
            os.getenv("STATUS_EVENT_OUTBOX_RETRY_DELAY_SECONDS", "5")
        ),
        status_event_outbox_backoff_multiplier=float(
            os.getenv("STATUS_EVENT_OUTBOX_BACKOFF_MULTIPLIER", "2.0")
        ),
        status_event_outbox_retry_max_delay_seconds=int(
            os.getenv("STATUS_EVENT_OUTBOX_RETRY_MAX_DELAY_SECONDS", "300")
        ),
        telegram_proxy_url=_strip_optional_env(os.getenv("TELEGRAM_PROXY_URL")),
        telegram_proxy_fallback_direct=_to_bool(
            os.getenv("TELEGRAM_PROXY_FALLBACK_DIRECT"), False
        ),
        ai_gateway_enabled=_to_bool(os.getenv("AI_GATEWAY_ENABLED"), False),
        ai_gateway_base_url=_strip_optional_env(os.getenv("AI_GATEWAY_BASE_URL")),
        ai_gateway_api_key=_strip_optional_env(os.getenv("AI_GATEWAY_API_KEY")),
        ai_gateway_timeout_seconds=int(os.getenv("AI_GATEWAY_TIMEOUT_SECONDS", "60")),
        ai_gateway_default_model=_strip_optional_env(os.getenv("AI_GATEWAY_DEFAULT_MODEL")),
        ai_gateway_default_response_language=_strip_optional_env(
            os.getenv("AI_GATEWAY_DEFAULT_RESPONSE_LANGUAGE")
        ),
        ai_image_generation_model=_strip_optional_env(os.getenv("AI_IMAGE_GENERATION_MODEL")),
        ai_image_generation_size=(
            os.getenv("AI_IMAGE_GENERATION_SIZE", "1536x1024").strip() or "1536x1024"
        ),
        ai_image_style_prompt=_strip_optional_env(os.getenv("AI_IMAGE_STYLE_PROMPT")),
        credentials_encryption_key=_strip_optional_env(os.getenv("CREDENTIALS_ENCRYPTION_KEY")),
        sentry_dsn=_strip_optional_env(os.getenv("SENTRY_DSN")),
        sentry_traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        media_storage_type=os.getenv("MEDIA_STORAGE_TYPE", "none").strip().lower() or "none",
        media_storage_path=_strip_optional_env(os.getenv("MEDIA_STORAGE_PATH")),
        media_base_url=_strip_optional_env(os.getenv("MEDIA_BASE_URL")),
        s3_endpoint_url=_first_optional_env("S3_ENDPOINT_URL", "MEDIA_S3_ENDPOINT_URL"),
        s3_region=(
            (os.getenv("S3_REGION") or os.getenv("MEDIA_S3_REGION") or "us-east-1").strip()
            or "us-east-1"
        ),
        s3_bucket=_first_optional_env("S3_BUCKET", "MEDIA_S3_BUCKET"),
        s3_access_key=_first_optional_env("S3_ACCESS_KEY", "MEDIA_S3_ACCESS_KEY"),
        s3_secret_key=_first_optional_env("S3_SECRET_KEY", "MEDIA_S3_SECRET_KEY"),
        s3_public_base_url=_first_optional_env(
            "S3_PUBLIC_BASE_URL", "S3_PUBLIC_BASE", "MEDIA_S3_PUBLIC_BASE_URL"
        ),
        postbridge_default_locale=(
            os.getenv("POSTBRIDGE_DEFAULT_LOCALE", "en").strip().lower() or "en"
        ),
        postbridge_default_timezone=(
            os.getenv("POSTBRIDGE_DEFAULT_TIMEZONE", "Europe/Moscow").strip()
            or "Europe/Moscow"
        ),
        agent_llm_base_url=_strip_optional_env(
            os.getenv("AGENT_LLM_BASE_URL") or os.getenv("AI_GATEWAY_BASE_URL")
        ),
        agent_llm_api_key=_strip_optional_env(
            os.getenv("AGENT_LLM_API_KEY") or os.getenv("AI_GATEWAY_API_KEY")
        ),
        agent_llm_default_model=_strip_optional_env(
            os.getenv("AGENT_LLM_DEFAULT_MODEL") or os.getenv("AI_GATEWAY_DEFAULT_MODEL")
        ),
        agent_llm_embedding_model=_strip_optional_env(
            os.getenv("AGENT_LLM_EMBEDDING_MODEL")
        ),
        agent_llm_max_tokens=int(os.getenv("AGENT_LLM_MAX_TOKENS", "2048")),
        agent_default_response_language=_strip_optional_env(
            os.getenv("AGENT_DEFAULT_RESPONSE_LANGUAGE")
            or os.getenv("AI_GATEWAY_DEFAULT_RESPONSE_LANGUAGE")
        ),
        agent_search_backend=(os.getenv("AGENT_SEARCH_BACKEND", "disabled").strip().lower() or "disabled"),
        agent_search_backends=tuple(
            part.strip().lower()
            for part in (os.getenv("AGENT_SEARCH_BACKENDS", "")).split(",")
            if part.strip()
        ),
        agent_search_max_results=int(os.getenv("AGENT_SEARCH_MAX_RESULTS", "5")),
        agent_search_query_variants=int(os.getenv("AGENT_SEARCH_QUERY_VARIANTS", "3")),
        agent_search_fetch_budget=int(os.getenv("AGENT_SEARCH_FETCH_BUDGET", "12")),
        agent_search_searxng_base_url=_strip_optional_env(os.getenv("AGENT_SEARCH_SEARXNG_BASE_URL")),
        agent_search_searxng_api_key=_strip_optional_env(os.getenv("AGENT_SEARCH_SEARXNG_API_KEY")),
        agent_search_language=_strip_optional_env(os.getenv("AGENT_SEARCH_LANGUAGE")),
        agent_search_allowed_domains=tuple(
            part.strip().lower()
            for part in (os.getenv("AGENT_SEARCH_ALLOWED_DOMAINS", "")).split(",")
            if part.strip()
        ),
        agent_search_blocked_domains=tuple(
            part.strip().lower()
            for part in (os.getenv("AGENT_SEARCH_BLOCKED_DOMAINS", "")).split(",")
            if part.strip()
        ),
        agent_search_blocked_source_types=tuple(
            part.strip().lower()
            for part in (os.getenv("AGENT_SEARCH_BLOCKED_SOURCE_TYPES", "")).split(",")
            if part.strip()
        ),
        agent_search_max_source_age_hours=int(os.getenv("AGENT_SEARCH_MAX_SOURCE_AGE_HOURS", "168")),
        agent_cleanup_retention_days=int(os.getenv("AGENT_CLEANUP_RETENTION_DAYS", "30")),
        agent_trace_retention_days=int(os.getenv("AGENT_TRACE_RETENTION_DAYS", "7")),
        agent_review_retention_days=int(os.getenv("AGENT_REVIEW_RETENTION_DAYS", "30")),
        agent_fingerprint_retention_days=int(os.getenv("AGENT_FINGERPRINT_RETENTION_DAYS", "30")),
        agent_vector_backend=(os.getenv("AGENT_VECTOR_BACKEND", "pgvector").strip().lower() or "pgvector"),
        agent_vector_dimensions=int(os.getenv("AGENT_VECTOR_DIMENSIONS", "1536")),
        agent_embedding_drift_reindex_interval_seconds=int(
            os.getenv("AGENT_EMBEDDING_DRIFT_REINDEX_INTERVAL_SECONDS", "21600")
        ),
        agent_embedding_drift_channel_limit=int(os.getenv("AGENT_EMBEDDING_DRIFT_CHANNEL_LIMIT", "20")),
        agent_embedding_drift_item_limit=int(os.getenv("AGENT_EMBEDDING_DRIFT_ITEM_LIMIT", "100")),
        agent_embedding_maintenance_interval_seconds=int(
            os.getenv("AGENT_EMBEDDING_MAINTENANCE_INTERVAL_SECONDS", "43200")
        ),
        agent_embedding_compaction_interval_seconds=int(
            os.getenv("AGENT_EMBEDDING_COMPACTION_INTERVAL_SECONDS", "86400")
        ),
        agent_embedding_candidate_retention_days=int(
            os.getenv("AGENT_EMBEDDING_CANDIDATE_RETENTION_DAYS", "30")
        ),
        agent_trace_policy=(os.getenv("AGENT_TRACE_POLICY", "summary").strip().lower() or "summary"),
        agent_trace_max_entries=int(os.getenv("AGENT_TRACE_MAX_ENTRIES", "100")),
        agent_trace_compaction_mode=(os.getenv("AGENT_TRACE_COMPACTION_MODE", "drop").strip().lower() or "drop"),
        agent_review_body_retention_days=int(os.getenv("AGENT_REVIEW_BODY_RETENTION_DAYS", "30")),
    )


def validate_base_settings(settings: Settings) -> None:
    """Проверяет обязательные настройки при старте. Выбрасывает ConfigurationError при ошибке."""
    if settings.postbridge_app_mode not in {"selfhost", "saas"}:
        raise ConfigurationError("POSTBRIDGE_APP_MODE must be one of: selfhost, saas.")
    if len(settings.postbridge_selfhost_tenant_id) != 36:
        raise ConfigurationError("POSTBRIDGE_SELFHOST_TENANT_ID must be a 36-character tenant id.")
    if not settings.database_url.strip():
        raise ConfigurationError("DATABASE_URL must be set.")
    if not settings.redis_url.strip():
        raise ConfigurationError("REDIS_URL must be set.")
    if settings.batch_import_run_max_retries < 0:
        raise ConfigurationError("BATCH_IMPORT_RUN_MAX_RETRIES must be >= 0.")
    if settings.batch_import_run_retry_delay_seconds < 0:
        raise ConfigurationError("BATCH_IMPORT_RUN_RETRY_DELAY_SECONDS must be >= 0.")
    if settings.batch_import_run_retry_backoff_multiplier < 1.0:
        raise ConfigurationError("BATCH_IMPORT_RUN_RETRY_BACKOFF_MULTIPLIER must be >= 1.0.")
    if settings.batch_import_run_retry_max_delay_seconds < 0:
        raise ConfigurationError("BATCH_IMPORT_RUN_RETRY_MAX_DELAY_SECONDS must be >= 0.")
    if settings.batch_import_run_stuck_timeout_seconds <= 0:
        raise ConfigurationError("BATCH_IMPORT_RUN_STUCK_TIMEOUT_SECONDS must be > 0.")
    if settings.status_event_webhook_timeout_seconds <= 0:
        raise ConfigurationError("STATUS_EVENT_WEBHOOK_TIMEOUT_SECONDS must be > 0.")
    if settings.status_event_outbox_batch_size <= 0:
        raise ConfigurationError("STATUS_EVENT_OUTBOX_BATCH_SIZE must be > 0.")
    if settings.status_event_outbox_max_retries <= 0:
        raise ConfigurationError("STATUS_EVENT_OUTBOX_MAX_RETRIES must be > 0.")
    if settings.status_event_outbox_retry_delay_seconds <= 0:
        raise ConfigurationError("STATUS_EVENT_OUTBOX_RETRY_DELAY_SECONDS must be > 0.")
    if settings.status_event_outbox_backoff_multiplier < 1.0:
        raise ConfigurationError("STATUS_EVENT_OUTBOX_BACKOFF_MULTIPLIER must be >= 1.0.")
    if settings.status_event_outbox_retry_max_delay_seconds <= 0:
        raise ConfigurationError("STATUS_EVENT_OUTBOX_RETRY_MAX_DELAY_SECONDS must be > 0.")
    if settings.ai_gateway_timeout_seconds <= 0:
        raise ConfigurationError("AI_GATEWAY_TIMEOUT_SECONDS must be > 0.")
    if not settings.credentials_encryption_key or not settings.credentials_encryption_key.strip():
        raise ConfigurationError(
            "CREDENTIALS_ENCRYPTION_KEY must be set (Fernet key: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")."
        )
    if settings.sentry_traces_sample_rate < 0 or settings.sentry_traces_sample_rate > 1:
        raise ConfigurationError("SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1.")
    try:
        from zoneinfo import ZoneInfo, available_timezones

        tz = settings.postbridge_default_timezone.strip()
        if tz not in available_timezones():
            raise ConfigurationError(
                f"POSTBRIDGE_DEFAULT_TIMEZONE must be a valid IANA name, got: {tz!r}"
            )
        ZoneInfo(tz)
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(
            f"POSTBRIDGE_DEFAULT_TIMEZONE invalid: {settings.postbridge_default_timezone!r}"
        ) from exc
    if settings.agent_search_backend not in {"disabled", "duckduckgo", "searxng"}:
        raise ConfigurationError(
            f"AGENT_SEARCH_BACKEND must be one of 'disabled', 'duckduckgo', 'searxng'; got {settings.agent_search_backend!r}"
        )
    if settings.agent_search_max_results <= 0:
        raise ConfigurationError("AGENT_SEARCH_MAX_RESULTS must be > 0.")
    if settings.agent_search_max_source_age_hours <= 0:
        raise ConfigurationError("AGENT_SEARCH_MAX_SOURCE_AGE_HOURS must be > 0.")
    if settings.agent_search_query_variants <= 0:
        raise ConfigurationError("AGENT_SEARCH_QUERY_VARIANTS must be > 0.")
    if settings.agent_search_fetch_budget <= 0:
        raise ConfigurationError("AGENT_SEARCH_FETCH_BUDGET must be > 0.")
    if settings.agent_embedding_drift_reindex_interval_seconds <= 0:
        raise ConfigurationError("AGENT_EMBEDDING_DRIFT_REINDEX_INTERVAL_SECONDS must be > 0.")
    if settings.agent_embedding_drift_channel_limit <= 0:
        raise ConfigurationError("AGENT_EMBEDDING_DRIFT_CHANNEL_LIMIT must be > 0.")
    if settings.agent_embedding_drift_item_limit <= 0:
        raise ConfigurationError("AGENT_EMBEDDING_DRIFT_ITEM_LIMIT must be > 0.")
    if settings.agent_embedding_maintenance_interval_seconds <= 0:
        raise ConfigurationError("AGENT_EMBEDDING_MAINTENANCE_INTERVAL_SECONDS must be > 0.")
    if settings.agent_embedding_compaction_interval_seconds <= 0:
        raise ConfigurationError("AGENT_EMBEDDING_COMPACTION_INTERVAL_SECONDS must be > 0.")
    if settings.agent_embedding_candidate_retention_days <= 0:
        raise ConfigurationError("AGENT_EMBEDDING_CANDIDATE_RETENTION_DAYS must be > 0.")
    if settings.agent_trace_policy not in {"summary", "full", "none"}:
        raise ConfigurationError("AGENT_TRACE_POLICY must be one of: summary, full, none.")
    if settings.agent_trace_max_entries <= 0:
        raise ConfigurationError("AGENT_TRACE_MAX_ENTRIES must be > 0.")
    if settings.agent_trace_compaction_mode not in {"drop", "summary"}:
        raise ConfigurationError("AGENT_TRACE_COMPACTION_MODE must be one of: drop, summary.")
    if settings.agent_review_body_retention_days <= 0:
        raise ConfigurationError("AGENT_REVIEW_BODY_RETENTION_DAYS must be > 0.")
    if settings.agent_search_backend == "searxng" and not settings.agent_search_searxng_base_url:
        raise ConfigurationError("AGENT_SEARCH_SEARXNG_BASE_URL must be set when AGENT_SEARCH_BACKEND=searxng.")
    if settings.agent_cleanup_retention_days <= 0:
        raise ConfigurationError("AGENT_CLEANUP_RETENTION_DAYS must be > 0.")
    if settings.agent_trace_retention_days <= 0:
        raise ConfigurationError("AGENT_TRACE_RETENTION_DAYS must be > 0.")
    if settings.agent_review_retention_days <= 0:
        raise ConfigurationError("AGENT_REVIEW_RETENTION_DAYS must be > 0.")
    if settings.agent_fingerprint_retention_days <= 0:
        raise ConfigurationError("AGENT_FINGERPRINT_RETENTION_DAYS must be > 0.")
    if settings.agent_vector_backend != "pgvector":
        raise ConfigurationError("AGENT_VECTOR_BACKEND must be 'pgvector'.")
    if settings.agent_vector_dimensions <= 0:
        raise ConfigurationError("AGENT_VECTOR_DIMENSIONS must be > 0.")
    if settings.agent_cleanup_retention_days <= 0:
        raise ConfigurationError("AGENT_CLEANUP_RETENTION_DAYS must be > 0.")
