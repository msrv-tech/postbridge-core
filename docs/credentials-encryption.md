# Channel Credential Encryption

Secrets in `channel_credentials.encrypted_secret` are stored as Fernet tokens. Core encrypts the complete UTF-8 JSON payload when credentials are written through the internal Service API.

## Environment

| Variable | Purpose |
| --- | --- |
| `CREDENTIALS_ENCRYPTION_KEY` | Fernet key, url-safe base64, 32 decoded bytes. Required at Core startup. |

Generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Key Rotation

Changing the key without re-encrypting existing rows makes stored credentials unreadable. For a greenfield or disposable environment, recreate credentials through `POST /internal/service/channels/ensure`.

## Errors

| Code | Meaning |
| --- | --- |
| `VALIDATION_CREDENTIALS_PLAINTEXT_STORAGE` | Plain JSON was found in the database; rewrite credentials through the API. |
| `VALIDATION_CREDENTIALS_DECRYPT_FAILED` | Corrupt data or an incorrect encryption key. |
