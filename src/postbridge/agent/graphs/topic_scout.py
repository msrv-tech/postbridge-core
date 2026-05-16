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
    canonical_angle_family,
    collect_topic_evidence,
    dedupe_mixed_list,
    get_channel_context,
    get_channel_style_profile,
    list_recent_publications,
    score_candidate_against_angles,
    search_similar_publications,
    shortlist_topic_angles,
    shortlist_topic_evidence,
    summarize_dedup,
    validate_platform_constraints,
)
from postbridge.config import get_settings


def build_topic_scout_graph(
    *,
    session: Session,
    provider: OpenAICompatibleProvider,
    max_candidates: int,
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
        topic_definition = state.get("topic_definition") or state.get("user_request") or "Find fresh topics"
        workspace_policy = state.get("workspace_policy") if isinstance(state.get("workspace_policy"), dict) else {}
        return {
            "channel_context": get_channel_context(session, tenant_id=tenant_id, channel_id=channel_id),
            "style_profile": get_channel_style_profile(session, tenant_id=tenant_id, channel_id=channel_id),
            "recent_publications": list_recent_publications(
                session, tenant_id=tenant_id, channel_id=channel_id, limit=10
            ),
            "source_bundle": collect_topic_evidence(
                session,
                tenant_id=tenant_id,
                channel_id=channel_id,
                topic=topic_definition,
                seed_urls=state.get("seed_urls") or [],
                workspace_policy=workspace_policy,
            ),
            "tool_trace": (state.get("tool_trace") or []) + [{"tool": "load_context"}],
        }

    def build_source_package(state: dict[str, Any]) -> dict[str, Any]:
        substate = source_package_graph.invoke(dict(state))
        shortlisted = substate.get("shortlisted_source_bundle") or []
        summary = substate.get("source_shortlist_summary") or {}
        return {
            "shortlisted_source_bundle": shortlisted,
            "source_shortlist_summary": summary,
            "source_package": substate.get("source_package") or {},
            "source_package_summary": substate.get("source_package_summary") or {},
            "tool_trace": substate.get("tool_trace") or (state.get("tool_trace") or []),
        }

    def shortlist_angles(state: dict[str, Any]) -> dict[str, Any]:
        sources = state.get("shortlisted_source_bundle") or state.get("source_bundle") or []
        shortlisted, summary = shortlist_topic_angles(
            sources,
            topic=state.get("topic_definition") or state.get("user_request"),
            max_angles=max(max_candidates, 3),
        )
        return {
            "shortlisted_angles": shortlisted,
            "angle_shortlist_summary": summary,
            "tool_trace": (state.get("tool_trace") or []) + [{"tool": "shortlist_angles", "summary": summary}],
        }

    def generate_candidates(state: dict[str, Any]) -> dict[str, Any]:
        topic_definition = state.get("topic_definition") or state.get("user_request") or "Find fresh topics"
        editorial_instructions = state.get("editorial_instructions")
        sources = state.get("shortlisted_source_bundle") or state.get("source_bundle") or []
        angles = state.get("shortlisted_angles") or []
        source_package = state.get("source_package") if isinstance(state.get("source_package"), dict) else {}
        image_mode = str(state.get("search_image_mode") or "").strip().lower()
        if image_mode not in {"none", "web_search", "generate"}:
            image_mode = "web_search" if state.get("image_request") else "none"
        image_request = bool(state.get("image_request")) and image_mode == "web_search"
        image_generation_request = image_mode == "generate"
        image_candidates = (
            source_package.get("image_candidates")
            if isinstance(source_package.get("image_candidates"), list)
            else []
        )
        allowed_image_urls = set(_dedupe_urls([item["url"] for item in image_candidates if isinstance(item, dict) and isinstance(item.get("url"), str)]))
        workspace_policy = state.get("workspace_policy") if isinstance(state.get("workspace_policy"), dict) else {}
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a topic scout for editorial teams. Return strict JSON with key candidates. "
                    "Candidates must be a list of objects with keys: topic, headline, body_markdown, summary, "
                    "why_now, style_fit_summary, source_bundle, scores, risk_flags, media_url, media_urls, cover_image_url. "
                    "body_markdown must contain only the publishable post draft itself. "
                    "Do not include editorial notes, formatting instructions, image instructions, "
                    "or meta sections such as Illustration, Post format, Notes to editor, "
                    "Иллюстрация, Формат поста, or similar headings. "
                    + (
                        "The workspace image mode is disabled; leave media_url, media_urls, and cover_image_url empty. "
                        if image_mode == "none"
                        else ""
                    )
                    + (
                        "If image support is requested, choose only from the provided Source image candidates. "
                        "Never invent image URLs. Set media_url and cover_image_url when one image fits, "
                        "and media_urls when multiple images are appropriate. "
                        if image_request
                        else ""
                    )
                    + (
                        "The workspace image mode asks for generated images. Do not choose or invent image URLs; "
                        "write candidates so a separate image generator can create a cover from the post context. "
                        if image_generation_request
                        else ""
                    )
                    + (
                        "Follow the workspace scouting policy when it does not conflict with the user's explicit request. "
                        if workspace_policy
                        else ""
                    )
                    + (
                        f"All natural-language fields must be written in {response_language}. "
                        "Do not switch to another language unless the user explicitly asks for it."
                        if response_language
                        else ""
                    )
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Workspace policy: {json.dumps(workspace_policy, ensure_ascii=False)}\n"
                    f"Channel context: {json.dumps(state['channel_context'], ensure_ascii=False)}\n"
                    f"Style profile: {json.dumps(state['style_profile'], ensure_ascii=False)}\n"
                    f"Recent publications: {json.dumps(state['recent_publications'], ensure_ascii=False)}\n"
                    f"Seed sources: {json.dumps(sources, ensure_ascii=False)}\n"
                    f"Structured source facts: {json.dumps(source_package.get('news_facts') or {}, ensure_ascii=False)}\n"
                    f"Source image candidates: {json.dumps(image_candidates, ensure_ascii=False)}\n"
                    f"Source shortlist summary: {json.dumps(state.get('source_shortlist_summary') or {}, ensure_ascii=False)}\n"
                    f"Topic angles: {json.dumps(angles, ensure_ascii=False)}\n"
                    f"Angle shortlist summary: {json.dumps(state.get('angle_shortlist_summary') or {}, ensure_ascii=False)}\n"
                    f"Goal: {topic_definition}\n"
                    + (
                        f"Editorial instructions: {editorial_instructions}\n"
                        if isinstance(editorial_instructions, str) and editorial_instructions.strip()
                        else ""
                    )
                    + (
                    f"Return {max_candidates} candidates."
                    )
                ),
            },
        ]
        payload, usage = provider.invoke_json(messages=messages)
        raw_candidates = payload.get("candidates")
        candidates = raw_candidates if isinstance(raw_candidates, list) else []
        normalized: list[dict[str, Any]] = []
        for candidate in candidates[:max_candidates]:
            if not isinstance(candidate, dict):
                continue
            dedup_summary, is_duplicate = summarize_dedup(
                state["recent_publications"],
                topic=candidate.get("topic"),
                headline=candidate.get("headline"),
            )
            candidate["dedup_summary"] = dedup_summary
            risk_flags = candidate.get("risk_flags") if isinstance(candidate.get("risk_flags"), list) else []
            if not sources:
                risk_flags.append("no_sources")
            if image_request and not image_candidates:
                risk_flags.append("missing_image_candidates")
            if image_generation_request:
                risk_flags.append("image_generation_requested")
            if is_duplicate:
                risk_flags.append("possible_duplicate")
            candidate["risk_flags"] = risk_flags
            if sources:
                source_bundle = candidate.get("source_bundle") if isinstance(candidate.get("source_bundle"), dict) else {}
                primary_sources = source_bundle.get("primary_sources") if isinstance(source_bundle.get("primary_sources"), list) else []
                source_bundle["primary_sources"] = list(
                    dict.fromkeys(primary_sources + [item.get("url") for item in sources if isinstance(item.get("url"), str)])
                )
                source_bundle["primary_sources_details"] = sources
                if angles:
                    source_bundle["topic_angles"] = angles
                if image_candidates:
                    source_bundle["image_candidates"] = image_candidates
                selection_context = (
                    dict(source_package.get("selection_context"))
                    if isinstance(source_package.get("selection_context"), dict)
                    else {}
                )
                if source_package.get("package_status"):
                    source_bundle["package_status"] = source_package["package_status"]
                selection_context["source_shortlist_summary"] = state.get("source_shortlist_summary") or {}
                selection_context["angle_shortlist_summary"] = state.get("angle_shortlist_summary") or {}
                source_bundle["selection_context"] = selection_context
                candidate["source_bundle"] = source_bundle
            _normalize_candidate_images(
                candidate,
                image_request=image_request,
                available_urls=allowed_image_urls,
                fallback_url=image_candidates[0]["url"] if image_candidates else None,
            )
            if image_generation_request:
                candidate["image_generation_request"] = {
                    "target": "cover",
                    "source": state.get("search_image_mode_source") or "task",
                }
            quality = analyze_source_quality(candidate.get("source_bundle"))
            source_bundle = candidate.get("source_bundle") if isinstance(candidate.get("source_bundle"), dict) else {}
            if quality["conflict_explanations"]:
                source_bundle["conflict_explanations"] = quality["conflict_explanations"]
                candidate["source_bundle"] = source_bundle
            scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
            scores["source_corroboration"] = quality["corroboration_score"]
            scores["source_freshness"] = quality["freshness_score"]
            scores["source_diversity"] = round(min(quality["unique_domain_count"] / 3.0, 1.0), 4)
            scores["source_quality"] = round(
                max(
                    quality["corroboration_score"] * 0.45
                    + quality["freshness_score"] * 0.3
                    + (1.0 - max(quality["disagreement_score"], quality["conflict_score"])) * 0.15
                    + quality["source_type_trust_score"] * 0.1,
                    0.0,
                ),
                4,
            )
            scores["source_conflict"] = quality["conflict_score"]
            scores["source_type_trust"] = quality["source_type_trust_score"]
            angle_alignment, matched_angle = score_candidate_against_angles(candidate, angles)
            scores["angle_alignment"] = angle_alignment
            candidate["scores"] = scores
            risk_flags = dedupe_mixed_list((candidate.get("risk_flags") or []) + quality["risk_flags"])
            candidate["risk_flags"] = risk_flags
            candidate["source_quality_summary"] = {
                "domains": quality["unique_domains"],
                "source_types": quality["source_types"],
                "corroboration_score": quality["corroboration_score"],
                "disagreement_score": quality["disagreement_score"],
                "conflict_score": quality["conflict_score"],
                "freshness_score": quality["freshness_score"],
                "source_type_trust_score": quality["source_type_trust_score"],
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
            if similarity["high_confidence_duplicate"]:
                risk_flags = dedupe_mixed_list(risk_flags + ["possible_duplicate"])
                candidate["risk_flags"] = risk_flags
            if not platform_constraints["ok"]:
                risk_flags = dedupe_mixed_list(risk_flags + ["platform_constraint_violation"])
                candidate["risk_flags"] = risk_flags
            if matched_angle:
                candidate["matched_angle"] = matched_angle
                source_bundle = candidate.get("source_bundle") if isinstance(candidate.get("source_bundle"), dict) else {}
                source_bundle["matched_angle"] = matched_angle
                source_bundle["matched_angle_family"] = canonical_angle_family(matched_angle)
                candidate["source_bundle"] = source_bundle
            normalized.append(candidate)
        return {
            "candidate_pool": normalized,
            "selected_candidates": normalized,
            "tool_trace": (state.get("tool_trace") or []) + [{"tool": "generate_candidates", "usage": usage}],
        }

    def rerank_candidates(state: dict[str, Any]) -> dict[str, Any]:
        candidates = list(state.get("candidate_pool") or [])
        if len(candidates) <= 1:
            return {
                "selected_candidates": candidates,
                "tool_trace": (state.get("tool_trace") or []) + [{"tool": "rerank_candidates", "skipped": True}],
            }
        query = state.get("topic_definition") or state.get("user_request") or "Find fresh topics"
        rerank_items = [
            {
                "headline": candidate.get("headline"),
                "topic": candidate.get("topic"),
                "summary": candidate.get("summary"),
                "dedup_summary": candidate.get("dedup_summary"),
                "style_fit_summary": candidate.get("style_fit_summary"),
                "risk_flags": candidate.get("risk_flags"),
                "source_quality_summary": candidate.get("source_quality_summary"),
                "scores": candidate.get("scores"),
            }
            for candidate in candidates
        ]
        ranked, usage = provider.invoke_rerank(
            query=query,
            items=rerank_items,
            top_k=len(candidates),
        )
        if not ranked:
            return {
                "selected_candidates": candidates,
                "tool_trace": (state.get("tool_trace") or []) + [{"tool": "rerank_candidates", "usage": usage, "empty": True}],
            }
        reordered: list[dict[str, Any]] = []
        seen: set[int] = set()
        combined_ranked: list[dict[str, Any]] = []
        for item in ranked:
            idx = item["index"]
            if 0 <= idx < len(candidates) and idx not in seen:
                candidate = dict(candidates[idx])
                scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
                scores["rerank"] = item["score"]
                source_quality = float(scores.get("source_quality") or 0.0)
                source_freshness = float(scores.get("source_freshness") or 0.0)
                source_corroboration = float(scores.get("source_corroboration") or 0.0)
                source_conflict = float(scores.get("source_conflict") or 0.0)
                source_type_trust = float(scores.get("source_type_trust") or 0.0)
                angle_alignment = float(scores.get("angle_alignment") or 0.0)
                scores["rerank_combined"] = round(
                    max(
                        float(item["score"]) * 0.55
                        + source_quality * 0.18
                        + source_freshness * 0.1
                        + source_corroboration * 0.05
                        + source_type_trust * 0.04
                        + angle_alignment * 0.08
                        - source_conflict * 0.15,
                        0.0,
                    ),
                    4,
                )
                candidate["scores"] = scores
                if item.get("reason"):
                    candidate["rerank_reason"] = item["reason"]
                combined_ranked.append(candidate)
                seen.add(idx)
        seen_angles: dict[str, int] = {}
        diversified: list[dict[str, Any]] = []
        for candidate in sorted(
            combined_ranked,
            key=lambda candidate: float((candidate.get("scores") or {}).get("rerank_combined") or 0.0),
            reverse=True,
        ):
            angle = str(candidate.get("matched_angle") or "").strip().lower()
            angle_family = canonical_angle_family(candidate.get("matched_angle"))
            angle_count = seen_angles.get(angle, 0) if angle else 0
            family_count = seen_angles.get(angle_family or "", 0) if angle_family else 0
            penalty = 0.0
            if angle:
                penalty += min(angle_count * 0.08, 0.16)
            if angle_family:
                penalty += min(family_count * 0.05, 0.1)
            scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
            scores["angle_diversity_penalty"] = round(penalty, 4)
            scores["rerank_diversified"] = round(max(float(scores.get("rerank_combined") or 0.0) - penalty, 0.0), 4)
            candidate["scores"] = scores
            diversified.append(candidate)
            if angle:
                seen_angles[angle] = angle_count + 1
            if angle_family:
                seen_angles[angle_family] = family_count + 1
        diversified.sort(
            key=lambda candidate: float((candidate.get("scores") or {}).get("rerank_diversified") or 0.0),
            reverse=True,
        )
        reordered.extend(diversified)
        for idx, candidate in enumerate(candidates):
            if idx not in seen:
                reordered.append(candidate)
        return {
            "selected_candidates": reordered,
            "tool_trace": (state.get("tool_trace") or []) + [{"tool": "rerank_candidates", "usage": usage}],
        }

    return compile_linear_graph(
        AgentState,
        [
            ("load_context", load_context),
            ("build_source_package", build_source_package),
            ("shortlist_angles", shortlist_angles),
            ("generate_candidates", generate_candidates),
            ("rerank_candidates", rerank_candidates),
        ],
    )
