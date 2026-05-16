"""Шифрование секретов channel_credentials (фаза 7)."""

from cryptography.fernet import Fernet

import pytest

from postbridge.domain.errors import ValidationError
from postbridge.infrastructure.crypto.credentials import (
    decrypt_credential_secret,
    encrypt_credential_secret,
)


def test_encrypt_decrypt_roundtrip() -> None:
    f = Fernet(Fernet.generate_key())
    plain = '{"token":"x","base_url":"https://example"}'
    ct = encrypt_credential_secret(plain, fernet=f)
    assert ct is not None
    assert "token" not in ct
    assert decrypt_credential_secret(ct, fernet=f) == plain


def test_decrypt_rejects_plaintext_json() -> None:
    f = Fernet(Fernet.generate_key())
    with pytest.raises(ValidationError) as ei:
        decrypt_credential_secret('{"a":1}', fernet=f)
    assert ei.value.code == "VALIDATION_CREDENTIALS_PLAINTEXT_STORAGE"
