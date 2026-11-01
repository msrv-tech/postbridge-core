"""Deterministic editorial operations exposed through the shared SDK agent."""

from postbridge.agent.tools import get_channel_context


def public_channel_context(session, *, tenant_id, channel_id):
    context = get_channel_context(session, tenant_id=tenant_id, channel_id=channel_id)
    # Connector configuration may contain credentials and is not model context.
    return {key: context[key] for key in ("channel_id", "platform", "title", "status")}
