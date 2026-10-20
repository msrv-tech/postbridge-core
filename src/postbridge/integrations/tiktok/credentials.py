from postbridge.api.schemas import TikTokCredentials
from postbridge.integrations._credential_json import decode_json_object


def decode_channel_credentials(row):
    data = decode_json_object(row)
    try:
        return TikTokCredentials.model_validate(data) if data else None
    except ValueError:
        return None
