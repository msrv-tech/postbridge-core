from postbridge.api.schemas import YouTubeCredentials
from postbridge.integrations._credential_json import decode_json_object


def decode_channel_credentials(row):
    data = decode_json_object(row)
    if not data:
        return None
    try:
        value = YouTubeCredentials.model_validate(data)
        return value if value.channel_id and (value.access_token or value.refresh_token and value.client_id and value.client_secret) else None
    except ValueError:
        return None
