from __future__ import annotations

from dataclasses import asdict, dataclass
from fnmatch import fnmatch
from typing import Any


@dataclass(slots=True)
class AgentWorkspacePolicy:
    editor_instructions: str = ""
    search_instructions: str = ""
    preferred_domains: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()
    blocked_url_patterns: tuple[str, ...] = ()


def workspace_policy_to_dict(policy: AgentWorkspacePolicy) -> dict[str, Any]:
    payload = asdict(policy)
    payload["preferred_domains"] = list(policy.preferred_domains)
    payload["blocked_domains"] = list(policy.blocked_domains)
    payload["blocked_url_patterns"] = list(policy.blocked_url_patterns)
    return payload


def apply_workspace_policy_overrides(
    policy: AgentWorkspacePolicy,
    overrides: dict[str, Any] | None,
) -> AgentWorkspacePolicy:
    raw = _workspace_policy_fragment(overrides)
    if not raw:
        return policy
    data = workspace_policy_to_dict(policy)
    if "editor_instructions" in raw:
        data["editor_instructions"] = _normalize_instruction(raw.get("editor_instructions"))
    if "search_instructions" in raw:
        data["search_instructions"] = _normalize_instruction(raw.get("search_instructions"))
    if "preferred_domains" in raw:
        data["preferred_domains"] = _normalize_domain_list(raw.get("preferred_domains"))
    if "blocked_domains" in raw:
        data["blocked_domains"] = _normalize_domain_list(raw.get("blocked_domains"))
    if "blocked_url_patterns" in raw:
        data["blocked_url_patterns"] = _normalize_pattern_list(raw.get("blocked_url_patterns"))
    return AgentWorkspacePolicy(
        editor_instructions=str(data["editor_instructions"]),
        search_instructions=str(data["search_instructions"]),
        preferred_domains=tuple(str(item) for item in data["preferred_domains"]),
        blocked_domains=tuple(str(item) for item in data["blocked_domains"]),
        blocked_url_patterns=tuple(str(item) for item in data["blocked_url_patterns"]),
    )


def extract_workspace_policy_payload(overrides: dict[str, Any] | None) -> dict[str, Any]:
    return _workspace_policy_fragment(overrides)


def matches_blocked_url_pattern(url: str, patterns: tuple[str, ...] | list[str]) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return False
    for raw in patterns:
        pattern = str(raw or "").strip().lower()
        if pattern and fnmatch(value, pattern):
            return True
    return False


def _workspace_policy_fragment(overrides: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(overrides, dict):
        return {}
    nested = overrides.get("workspace_policy")
    if isinstance(nested, dict):
        return dict(nested)
    legacy_keys = {
        "editor_instructions",
        "search_instructions",
        "preferred_domains",
        "blocked_domains",
        "blocked_url_patterns",
    }
    if any(key in overrides for key in legacy_keys):
        return {key: overrides.get(key) for key in legacy_keys if key in overrides}
    return {}


def _normalize_instruction(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()[:4000]


def _normalize_domain_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = str(item or "").strip().lower()
        normalized = normalized.removeprefix("https://").removeprefix("http://").strip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out[:50]


def _normalize_pattern_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out[:50]
