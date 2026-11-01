"""Core adapter for the shared, database-independent Postbridge SDK runner."""
from postbridge_agent_runtime import (
    AGENT_NAME, AgentExecution, AgentMessage, AgentResult, AgentTool, ProviderReceipt, ToolEvent,
    run_postbridge_agent as run_shared_agent,
)
from postbridge_agent_runtime.errors import AgentExecutionError, AgentValidationError
from postbridge.domain.errors import ExternalApiError, PostbridgeError, ValidationError


async def run_postbridge_agent(execution, *, before_provider_call=None, on_provider_receipt=None, transport=None):
    try:
        return await run_shared_agent(execution, before_provider_call=before_provider_call,
            on_provider_receipt=on_provider_receipt, transport=transport, tool_error_types=(PostbridgeError,))
    except AgentValidationError as exc:
        raise ValidationError(code=exc.code, message=exc.message, details=exc.details) from exc
    except AgentExecutionError as exc:
        raise ExternalApiError(code=exc.code, message=exc.message, source="agent_llm",
            retryable=False, details=exc.details) from exc
