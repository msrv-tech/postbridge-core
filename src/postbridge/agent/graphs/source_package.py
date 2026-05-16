from __future__ import annotations

import struct
from typing import Any

import httpx
from sqlalchemy.orm import Session

from postbridge.agent.runtime import compile_linear_graph
from postbridge.agent.state import AgentState
from postbridge.agent.retrieval import collect_topic_sources
from postbridge.agent.tools import (
    analyze_source_quality,
    collect_seed_sources,
    extract_news_facts,
    shortlist_topic_evidence,
)

_MIN_IMAGE_WIDTH = 320
_MIN_IMAGE_HEIGHT = 180


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


def _is_large_enough_image(width: int | None, height: int | None) -> bool:
    if width is None or height is None:
        return True
    return width >= _MIN_IMAGE_WIDTH and height >= _MIN_IMAGE_HEIGHT


def _probe_image_dimensions(url: str, *, timeout_seconds: float = 5.0) -> tuple[int | None, int | None]:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds), follow_redirects=True) as client:
            response = client.get(
                url,
                headers={
                    "User-Agent": "postbridge-agent/1.0",
                    "Range": "bytes=0-65535",
                },
            )
    except httpx.HTTPError:
        return None, None
    if response.status_code >= 400:
        return None, None
    return _parse_image_dimensions(response.content[:65536])


def _parse_image_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    if len(payload) >= 24 and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", payload[16:24])
        return int(width), int(height)
    if len(payload) >= 10 and payload[:6] in {b"GIF87a", b"GIF89a"}:
        width, height = struct.unpack("<HH", payload[6:10])
        return int(width), int(height)
    if len(payload) >= 30 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        chunk = payload[12:16]
        if chunk == b"VP8X" and len(payload) >= 30:
            width = 1 + int.from_bytes(payload[24:27], "little")
            height = 1 + int.from_bytes(payload[27:30], "little")
            return width, height
        if chunk == b"VP8 " and len(payload) >= 30:
            start = payload.find(b"\x9d\x01\x2a")
            if start != -1 and len(payload) >= start + 7:
                width, height = struct.unpack("<HH", payload[start + 3 : start + 7])
                return int(width), int(height)
        if chunk == b"VP8L" and len(payload) >= 25:
            bits = int.from_bytes(payload[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return width, height
    if len(payload) >= 4 and payload[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(payload):
            if payload[offset] != 0xFF:
                offset += 1
                continue
            marker = payload[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(payload):
                break
            segment_length = int.from_bytes(payload[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(payload):
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            } and segment_length >= 7:
                height = int.from_bytes(payload[offset + 3 : offset + 5], "big")
                width = int.from_bytes(payload[offset + 5 : offset + 7], "big")
                return width, height
            offset += segment_length
    return None, None


def _collect_image_candidates(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            continue
        urls: list[str] = []
        preview = source.get("preview_image_url")
        if isinstance(preview, str) and preview.strip():
            urls.append(preview.strip())
        raw_list = source.get("image_urls")
        if isinstance(raw_list, list):
            urls.extend(str(item).strip() for item in raw_list if isinstance(item, str) and item.strip())
        for rank, url in enumerate(_dedupe_urls(urls), start=1):
            if url in seen:
                continue
            width, height = _probe_image_dimensions(url)
            if not _is_large_enough_image(width, height):
                continue
            seen.add(url)
            item = {
                "url": url,
                "source_url": source.get("url"),
                "source_title": source.get("title"),
                "source_rank": index,
                "image_rank": rank,
            }
            if width is not None:
                item["width"] = width
            if height is not None:
                item["height"] = height
            out.append(item)
            if len(out) >= 8:
                return out
    return out


def build_source_package_subgraph(*, session: Session) -> Any:
    del session

    def collect_sources(state: dict[str, Any]) -> dict[str, Any]:
        topic = state.get("topic_definition") or state.get("user_request")
        workspace_policy = state.get("workspace_policy") if isinstance(state.get("workspace_policy"), dict) else {}
        existing_sources = state.get("source_bundle")
        if isinstance(existing_sources, list) and existing_sources:
            sources = [dict(item) for item in existing_sources if isinstance(item, dict)]
        else:
            sources = collect_seed_sources(state.get("seed_urls") or [])
        if not sources and isinstance(topic, str) and topic.strip():
            sources = collect_topic_sources(
                topic.strip(),
                seed_urls=[],
                preferred_domains={
                    str(item).strip().lower()
                    for item in (workspace_policy.get("preferred_domains") or [])
                    if isinstance(item, str) and str(item).strip()
                },
                blocked_domains={
                    str(item).strip().lower()
                    for item in (workspace_policy.get("blocked_domains") or [])
                    if isinstance(item, str) and str(item).strip()
                },
                blocked_url_patterns=[
                    str(item).strip()
                    for item in (workspace_policy.get("blocked_url_patterns") or [])
                    if isinstance(item, str) and str(item).strip()
                ],
            )
        return {
            "source_bundle": sources,
            "tool_trace": (state.get("tool_trace") or []) + [{"tool": "source_package.collect_sources"}],
        }

    def shortlist_sources(state: dict[str, Any]) -> dict[str, Any]:
        shortlisted, summary = shortlist_topic_evidence(
            state.get("source_bundle") or [],
            topic=state.get("topic_definition") or state.get("user_request"),
            max_sources=6,
            max_per_domain=2,
        )
        return {
            "shortlisted_source_bundle": shortlisted,
            "source_shortlist_summary": summary,
            "tool_trace": (
                (state.get("tool_trace") or [])
                + [{"tool": "source_package.shortlist_sources", "summary": summary}]
            ),
        }

    def prepare_source_package(state: dict[str, Any]) -> dict[str, Any]:
        sources = state.get("shortlisted_source_bundle") or state.get("source_bundle") or []
        image_mode = str(state.get("search_image_mode") or "").strip().lower()
        if image_mode not in {"none", "web_search", "generate"}:
            image_mode = "web_search" if state.get("image_request") else "none"
        requested_images = bool(state.get("image_request")) and image_mode == "web_search"
        approved_image_urls = {
            str(item).strip()
            for item in (state.get("approved_image_urls") or [])
            if isinstance(item, str) and str(item).strip()
        }
        image_candidates = (
            _collect_image_candidates(sources)
            if image_mode == "web_search" and (requested_images or approved_image_urls)
            else []
        )
        if approved_image_urls:
            image_candidates = [
                item for item in image_candidates if str(item.get("url") or "").strip() in approved_image_urls
            ]
        quality = analyze_source_quality({"seed_sources": sources})
        package_summary = {
            "requested_images": requested_images,
            "search_image_mode": image_mode,
            "seed_url_count": len(state.get("seed_urls") or []),
            "discovered_source_count": len(state.get("source_bundle") or []),
            "selected_source_count": len(sources),
            "image_candidate_count": len(image_candidates),
            "preferred_domain_count": len(
                [
                    item
                    for item in ((state.get("workspace_policy") or {}).get("preferred_domains") or [])
                    if isinstance(item, str) and item.strip()
                ]
            ),
            "blocked_domain_count": len(
                [
                    item
                    for item in ((state.get("workspace_policy") or {}).get("blocked_domains") or [])
                    if isinstance(item, str) and item.strip()
                ]
            ),
            "unique_domains": quality["unique_domains"],
            "corroboration_score": quality["corroboration_score"],
            "freshness_score": quality["freshness_score"],
            "risk_flags": quality["risk_flags"],
        }
        source_package = {
            "primary_sources": [
                item.get("url")
                for item in sources
                if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("url")
            ],
            "seed_sources": sources,
            "primary_sources_details": sources,
            "news_facts": extract_news_facts(
                sources,
                topic=state.get("topic_definition") or state.get("user_request"),
            ),
            "image_candidates": image_candidates,
            "selection_context": {
                "source_shortlist_summary": state.get("source_shortlist_summary") or {},
                "source_package_summary": package_summary,
            },
            "package_status": "ready",
        }
        if quality["conflict_explanations"]:
            source_package["conflict_explanations"] = quality["conflict_explanations"]
        return {
            "source_package": source_package,
            "source_package_summary": package_summary,
            "tool_trace": (
                (state.get("tool_trace") or [])
                + [{"tool": "source_package.prepare_source_package", "summary": package_summary}]
            ),
        }

    return compile_linear_graph(
        AgentState,
        [
            ("collect_sources", collect_sources),
            ("shortlist_sources", shortlist_sources),
            ("prepare_source_package", prepare_source_package),
        ],
    )
