from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    tenant_id: str
    channel_id: str
    content_item_id: str | None
    agent_task_id: str | None
    agent_run_id: str
    mode: str
    user_request: str | None
    topic_definition: str | None
    editorial_instructions: str | None
    image_request: bool
    seed_urls: list[str]
    approved_image_urls: list[str]
    workspace_policy: dict[str, Any]
    source_bundle: list[dict[str, Any]]
    shortlisted_source_bundle: list[dict[str, Any]]
    source_shortlist_summary: dict[str, Any]
    source_package: dict[str, Any]
    source_package_summary: dict[str, Any]
    shortlisted_angles: list[dict[str, Any]]
    angle_shortlist_summary: dict[str, Any]
    model: str | None
    provider_type: str | None
    current_draft: dict[str, Any] | None
    channel_context: dict[str, Any]
    style_profile: dict[str, Any]
    recent_publications: list[dict[str, Any]]
    candidate_pool: list[dict[str, Any]]
    selected_candidates: list[dict[str, Any]]
    tool_trace: list[dict[str, Any]]
    review_items: list[dict[str, Any]]
    result: dict[str, Any]
    errors: list[dict[str, Any]]
