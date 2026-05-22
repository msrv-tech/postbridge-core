from postbridge.config import get_settings


def test_settings_version_falls_back_when_package_metadata_missing(monkeypatch):
    from importlib.metadata import PackageNotFoundError
    from postbridge import config

    monkeypatch.delenv("POSTBRIDGE_VERSION", raising=False)

    def missing_version(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(config, "version", missing_version)

    assert get_settings().postbridge_version == "0.1.0"


def test_selfhost_ai_timeouts_default_to_long_values(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.delenv("AI_GATEWAY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AI_IMAGE_GENERATION_TIMEOUT_SECONDS", raising=False)

    settings = get_settings()

    assert settings.ai_gateway_timeout_seconds == 300
    assert settings.ai_image_generation_timeout_seconds == 300


def test_saas_ai_timeouts_keep_shorter_defaults(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    monkeypatch.delenv("AI_GATEWAY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AI_IMAGE_GENERATION_TIMEOUT_SECONDS", raising=False)

    settings = get_settings()

    assert settings.ai_gateway_timeout_seconds == 60
    assert settings.ai_image_generation_timeout_seconds == 120


def test_selfhost_media_storage_defaults_to_local(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("CORE_BASE_URL", "http://localhost:9000")
    monkeypatch.delenv("MEDIA_STORAGE_TYPE", raising=False)
    monkeypatch.delenv("MEDIA_STORAGE_PATH", raising=False)
    monkeypatch.delenv("MEDIA_BASE_URL", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)

    settings = get_settings()

    assert settings.media_storage_type == "local"
    assert settings.media_storage_path == "/var/postbridge/media"
    assert settings.media_base_url == "http://localhost:9000/media"


def test_selfhost_incomplete_s3_media_storage_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "selfhost")
    monkeypatch.setenv("CORE_BASE_URL", "http://localhost:9000")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "s3")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("MEDIA_S3_BUCKET", raising=False)

    settings = get_settings()

    assert settings.media_storage_type == "local"
    assert settings.media_base_url == "http://localhost:9000/media"


def test_saas_incomplete_s3_media_storage_stays_disabled(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "s3")
    monkeypatch.setenv("S3_BUCKET", "postbridge-media")
    monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MEDIA_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_SECRET_KEY", raising=False)
    monkeypatch.delenv("MEDIA_S3_SECRET_KEY", raising=False)

    settings = get_settings()

    assert settings.media_storage_type == "none"


def test_saas_complete_s3_media_storage_is_enabled(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "s3")
    monkeypatch.setenv("S3_BUCKET", "postbridge-media")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.delenv("MEDIA_S3_BUCKET", raising=False)

    settings = get_settings()

    assert settings.media_storage_type == "s3"


def test_saas_missing_s3_bucket_media_storage_stays_disabled(monkeypatch):
    monkeypatch.setenv("POSTBRIDGE_APP_MODE", "saas")
    monkeypatch.setenv("MEDIA_STORAGE_TYPE", "s3")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("MEDIA_S3_BUCKET", raising=False)

    settings = get_settings()

    assert settings.media_storage_type == "none"
