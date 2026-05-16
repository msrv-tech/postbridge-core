"""Fernet-шифрование JSON-секретов в channel_credentials.encrypted_secret (фаза 7)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from postbridge.config import get_settings
from postbridge.domain.errors import ConfigurationError, ValidationError


def get_fernet_for_credentials() -> Fernet:
    """Возвращает Fernet по CREDENTIALS_ENCRYPTION_KEY из настроек."""
    settings = get_settings()
    key = (settings.credentials_encryption_key or "").strip()
    if not key:
        raise ConfigurationError("CREDENTIALS_ENCRYPTION_KEY is not set.")
    try:
        return Fernet(key.encode("ascii"))
    except Exception as e:
        raise ConfigurationError(
            "CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key (url-safe base64).",
            details={"error": str(e)},
        ) from e


def encrypt_credential_secret(plaintext: str | None, *, fernet: Fernet | None = None) -> str | None:
    """Шифрует UTF-8 строку (обычно JSON с токенами). None/пусто → None."""
    if plaintext is None or not str(plaintext).strip():
        return None
    f = fernet or get_fernet_for_credentials()
    token = f.encrypt(str(plaintext).encode("utf-8"))
    return token.decode("ascii")


def decrypt_credential_secret(blob: str | None, *, fernet: Fernet | None = None) -> str:
    """Расшифровывает значение из БД. Пустое → \"\". Plaintext JSON в БД — ошибка валидации."""
    if blob is None or not str(blob).strip():
        return ""
    s = str(blob).strip()
    f = fernet or get_fernet_for_credentials()
    try:
        return f.decrypt(s.encode("ascii")).decode("utf-8")
    except InvalidToken:
        lead = s.lstrip()
        if lead.startswith("{") or lead.startswith("["):
            raise ValidationError(
                code="VALIDATION_CREDENTIALS_PLAINTEXT_STORAGE",
                message="channel credential secret stored as plaintext; save again via internal service API",
                details={},
            )
        raise ValidationError(
            code="VALIDATION_CREDENTIALS_DECRYPT_FAILED",
            message="cannot decrypt channel credential (wrong CREDENTIALS_ENCRYPTION_KEY or corrupted data)",
            details={},
        )


def decode_channel_credential_raw(row) -> str:
    """Берёт encrypted_secret или meta_json, возвращает расшифрованный UTF-8 текст для json.loads."""
    if row is None:
        return ""
    raw = (row.encrypted_secret or row.meta_json or "").strip()
    if not raw:
        return ""
    return decrypt_credential_secret(raw)
