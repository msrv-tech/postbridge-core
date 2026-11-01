"""Portable execution errors, without application or database imports."""
from typing import Any


class AgentRuntimeError(Exception):
    def __init__(self, *, code: str, message: str, details: dict[str, Any] | None = None,
            source: str = "agent_llm", retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}
        self.source, self.retryable = source, retryable


class AgentValidationError(AgentRuntimeError):
    pass


class AgentExecutionError(AgentRuntimeError):
    pass
