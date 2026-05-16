from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from postbridge.agent.graphs.source_package import build_source_package_subgraph
from postbridge.agent.providers.openai_compatible import OpenAICompatibleProvider
from postbridge.agent.runtime import compile_linear_graph
from postbridge.agent.state import AgentState
from postbridge.agent.tools import (
    analyze_source_quality,
    dedupe_mixed_list,
    get_channel_context,
    get_channel_style_profile,
    list_recent_publications,
    search_similar_publications,
    summarize_dedup,
    validate_platform_constraints,
)
from postbridge.config import get_settings
from postbridge.models.domain import ContentItemOrm


def build_post_copilot_graph(
    *,
    session: Session,
    provider: OpenAICompatibleProvider,
) -> Any:
    response_language = get_settings().agent_default_response_language
    source_package_graph = build_source_package_subgraph(session=session)

    def _dedupe_urls(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in items:
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    def _normalize_candidate_images(
        candidate: dict[str, Any],
        *,
        image_request: bool,
        available_urls: set[str],
        fallback_url: str | None,
    ) -> None:
        media_url = candidate.get("media_url")
        media_url_value = media_url.strip() if isinstance(media_url, str) else None
        if not media_url_value or media_url_value not in available_urls:
            media_url_value = None

        media_urls: list[str] = []
        raw_media_urls = candidate.get("media_urls")
        if isinstance(raw_media_urls, list):
            for item in raw_media_urls:
                if isinstance(item, str):
                    value = item.strip()
                    if value and value in available_urls and value not in media_urls:
                        media_urls.append(value)

        cover_image_url = candidate.get("cover_image_url")
        cover_image_value = cover_image_url.strip() if isinstance(cover_image_url, str) else None
        if not cover_image_value or cover_image_value not in available_urls:
            cover_image_value = None

        if image_request and not media_url_value and not media_urls and not cover_image_value and fallback_url:
            media_url_value = fallback_url
            media_urls = [fallback_url]
            cover_image_value = fallback_url

        if media_url_value and not media_urls:
            media_urls = [media_url_value]
        if cover_image_value is None and media_url_value:
            cover_image_value = media_url_value
        if media_url_value is None and media_urls:
            media_url_value = media_urls[0]

        if media_url_value:
            candidate["media_url"] = media_url_value
        else:
            candidate.pop("media_url", None)
        if media_urls:
            candidate["media_urls"] = media_urls
        else:
            candidate.pop("media_urls", None)
        if cover_image_value:
            candidate["cover_image_url"] = cover_image_value
        else:
            candidate.pop("cover_image_url", None)

    def load_context(state: dict[str, Any]) -> dict[str, Any]:
        tenant_id = state["tenant_id"]
        channel_id = state["channel_id"]
        current_draft = None
        content_item_id = state.get("content_item_id")
        if isinstance(content_item_id, str) and content_item_id.strip():
            row = session.get(ContentItemOrm, content_item_id)
            if row is not None and row.tenant_id == tenant_id:
                extra: dict[str, Any] = {}
                if isinstance(row.body_structured_json, str) and row.body_structured_json.strip():
                    try:
                        loaded = json.loads(row.body_structured_json)
                    except json.JSONDecodeError:
                        loaded = {}
                    if isinstance(loaded, dict):
                        extra = loaded
                current_draft = {
                    "content_item_id": row.id,
                    "title": row.title,
                    "body_markdown": row.body_markdown,
                    "status": row.status,
                    "media_url": row.media_url,
                    "media_urls": row.media_urls,
                    "cover_image_url": extra.get("cover_image_url"),
                }
        return {
            "channel_context": get_channel_context(session, tenant_id=tenant_id, channel_id=channel_id),
            "style_profile": get_channel_style_profile(session, tenant_id=tenant_id, channel_id=channel_id),
            "recent_publications": list_recent_publications(
                session, tenant_id=tenant_id, channel_id=channel_id, limit=10
            ),
            "current_draft": current_draft,
            "tool_trace": (state.get("tool_trace") or []) + [{"tool": "load_context"}],
        }

    def build_source_package(state: dict[str, Any]) -> dict[str, Any]:
        substate = source_package_graph.invoke(dict(state))
        return {
            "source_bundle": substate.get("source_bundle") or [],
            "shortlisted_source_bundle": substate.get("shortlisted_source_bundle") or [],
            "source_shortlist_summary": substate.get("source_shortlist_summary") or {},
            "source_package": substate.get("source_package") or {},
            "source_package_summary": substate.get("source_package_summary") or {},
            "tool_trace": substate.get("tool_trace") or (state.get("tool_trace") or []),
        }

    def draft_candidate(state: dict[str, Any]) -> dict[str, Any]:
        channel = state["channel_context"]
        style = state["style_profile"]
        recent = state["recent_publications"]
        workspace_policy = state.get("workspace_policy") if isinstance(state.get("workspace_policy"), dict) else {}
        source_package = state.get("source_package") if isinstance(state.get("source_package"), dict) else {}
        sources = source_package.get("primary_sources_details") if isinstance(source_package.get("primary_sources_details"), list) else []
        image_request = bool(state.get("image_request"))
        image_candidates = (
            source_package.get("image_candidates")
            if isinstance(source_package.get("image_candidates"), list)
            else []
        )
        current_draft = state.get("current_draft") if isinstance(state.get("current_draft"), dict) else None
        existing_image_urls: list[str] = []
        if current_draft:
            if isinstance(current_draft.get("media_url"), str) and current_draft["media_url"].strip():
                existing_image_urls.append(current_draft["media_url"].strip())
            raw_existing_media_urls = current_draft.get("media_urls")
            if isinstance(raw_existing_media_urls, list):
                existing_image_urls.extend(
                    item.strip() for item in raw_existing_media_urls if isinstance(item, str) and item.strip()
                )
            if (
                isinstance(current_draft.get("cover_image_url"), str)
                and current_draft["cover_image_url"].strip()
            ):
                existing_image_urls.append(current_draft["cover_image_url"].strip())
        allowed_image_urls = set(_dedupe_urls([item["url"] for item in image_candidates] + existing_image_urls))
        prompt = state.get("user_request") or "Create a post draft"
        has_existing_draft = bool(current_draft)
        existing_context = ""
        if has_existing_draft:
            existing_context = (
                "Existing draft metadata:\n"
                f"{json.dumps({'content_item_id': current_draft.get('content_item_id'), 'status': current_draft.get('status'), 'media_url': current_draft.get('media_url'), 'media_urls': current_draft.get('media_urls'), 'cover_image_url': current_draft.get('cover_image_url')}, ensure_ascii=False)}\n"
                f"Current title:\n{current_draft.get('title') or ''}\n"
                f"Current body_markdown:\n{current_draft.get('body_markdown') or ''}\n"
                "This is an edit request for the current draft, not a request for a brand new post.\n"
                "Editing rules:\n"
                "1. Use the current draft as the source text.\n"
                "2. Preserve every part the user did not ask to change.\n"
                "3. Prefer minimal, local edits instead of rewriting the whole draft.\n"
                "4. Never answer with acknowledgements like 'Принял запрос' or 'Вот обновлённый вариант'.\n"
                "5. body_markdown must contain the full revised post, ready to publish.\n"
                "6. If the user asks to remove one paragraph or adjust tone, keep the remaining text intact wherever possible.\n"
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an editorial copilot. Return strict JSON with keys: "
                    "topic, headline, body_markdown, summary, why_now, style_fit_summary, "
                    "source_bundle, scores, risk_flags, media_url, media_urls, cover_image_url, image_selection_notes. "
                    + (
                        "If an existing draft is provided, you must revise that exact draft rather than rewrite from scratch. "
                        "Treat the user's request as an edit instruction applied to the current draft. "
                        "Preserve unchanged sections verbatim whenever possible, and only modify the parts needed to satisfy the request. "
                        "Do not collapse the draft into a short note, acknowledgement, or summary. "
                        if has_existing_draft
                        else ""
                    )
                    + (
                        f"All natural-language fields must be written in {response_language}. "
                        "Do not switch to another language unless the user explicitly asks for it."
                        if response_language
                        else ""
                    )
                    + (
                        "If the user asks to add or update an image, choose only from the provided Source image candidates "
                        "or keep existing draft image URLs. Never invent new image URLs. "
                        "Set media_url and cover_image_url when you select one image, and media_urls when multiple images are appropriate. "
                        if image_request
                        else ""
                    )
                    + (
                        "Follow the workspace editorial policy when it does not conflict with the user's explicit request. "
                        if workspace_policy
                        else ""
                    )
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Workspace policy: {json.dumps(workspace_policy, ensure_ascii=False)}\n"
                    f"Channel context: {json.dumps(channel, ensure_ascii=False)}\n"
                    f"Style profile: {json.dumps(style, ensure_ascii=False)}\n"
                    f"Recent publications: {json.dumps(recent, ensure_ascii=False)}\n"
                    f"Seed sources: {json.dumps(sources, ensure_ascii=False)}\n"
                    f"Structured source facts: {json.dumps(source_package.get('news_facts') or {}, ensure_ascii=False)}\n"
                    f"Source image candidates: {json.dumps(image_candidates, ensure_ascii=False)}\n"
                    f"{existing_context}"
                    f"Instruction: {prompt}"
                ),
            },
        ]
        candidate, usage = provider.invoke_json(messages=messages)
        dedup_summary, is_duplicate = summarize_dedup(
            recent,
            topic=candidate.get("topic"),
            headline=candidate.get("headline"),
        )
        scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
        scores.setdefault("confidence", 0.7)
        candidate["scores"] = scores
        candidate["dedup_summary"] = dedup_summary
        risk_flags = candidate.get("risk_flags") if isinstance(candidate.get("risk_flags"), list) else []
        if is_duplicate:
            risk_flags.append("possible_duplicate")
        candidate["risk_flags"] = risk_flags
        source_bundle = candidate.get("source_bundle") if isinstance(candidate.get("source_bundle"), dict) else {}
        if sources:
            source_bundle.setdefault(
                "primary_sources",
                source_package.get("primary_sources")
                if isinstance(source_package.get("primary_sources"), list)
                else [
                    item.get("url")
                    for item in sources
                    if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("url")
                ],
            )
            source_bundle["seed_sources"] = sources
            source_bundle["primary_sources_details"] = sources
        if image_candidates:
            source_bundle["image_candidates"] = image_candidates
        selection_context = source_package.get("selection_context")
        if isinstance(selection_context, dict) and selection_context:
            source_bundle["selection_context"] = selection_context
        if source_package.get("package_status"):
            source_bundle["package_status"] = source_package["package_status"]
        if source_bundle:
            candidate["source_bundle"] = source_bundle
        _normalize_candidate_images(
            candidate,
            image_request=image_request,
            available_urls=allowed_image_urls,
            fallback_url=image_candidates[0]["url"] if image_candidates else None,
        )
        quality = analyze_source_quality(candidate.get("source_bundle"))
        source_bundle = candidate.get("source_bundle") if isinstance(candidate.get("source_bundle"), dict) else {}
        if quality["conflict_explanations"]:
            source_bundle["conflict_explanations"] = quality["conflict_explanations"]
            candidate["source_bundle"] = source_bundle
        scores["source_corroboration"] = quality["corroboration_score"]
        scores["source_freshness"] = quality["freshness_score"]
        scores["source_diversity"] = round(min(quality["unique_domain_count"] / 3.0, 1.0), 4)
        scores["source_quality"] = round(
            max(
                quality["corroboration_score"] * 0.45
                + quality["freshness_score"] * 0.3
                + (1.0 - max(quality["disagreement_score"], quality["conflict_score"])) * 0.25,
                0.0,
            ),
            4,
        )
        scores["source_conflict"] = quality["conflict_score"]
        candidate["source_quality_summary"] = {
            "domains": quality["unique_domains"],
            "corroboration_score": quality["corroboration_score"],
            "disagreement_score": quality["disagreement_score"],
            "conflict_score": quality["conflict_score"],
            "freshness_score": quality["freshness_score"],
            "conflict_explanations": quality["conflict_explanations"],
        }
        similarity = search_similar_publications(
            session,
            tenant_id=state["tenant_id"],
            channel_id=state["channel_id"],
            title=candidate.get("headline"),
            body_markdown=candidate.get("body_markdown"),
            source_bundle=candidate.get("source_bundle"),
        )
        platform_constraints = validate_platform_constraints(
            session,
            tenant_id=state["tenant_id"],
            channel_id=state["channel_id"],
            candidate=candidate,
        )
        candidate["similar_publications_summary"] = similarity
        candidate["platform_constraint_summary"] = platform_constraints
        extra_risks: list[str] = []
        if similarity["high_confidence_duplicate"]:
            extra_risks.append("possible_duplicate")
        if not platform_constraints["ok"]:
            extra_risks.append("platform_constraint_violation")
        candidate["risk_flags"] = dedupe_mixed_list(risk_flags + quality["risk_flags"] + extra_risks)
        return {
            "candidate_pool": [candidate],
            "selected_candidates": [candidate],
            "tool_trace": (state.get("tool_trace") or []) + [{"tool": "draft_candidate", "usage": usage}],
        }

    return compile_linear_graph(
        AgentState,
        [
            ("load_context", load_context),
            ("build_source_package", build_source_package),
            ("draft_candidate", draft_candidate),
        ],
    )
