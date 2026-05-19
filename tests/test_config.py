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
