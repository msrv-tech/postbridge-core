"""Unit-тесты разбора SSE для стриминга chat/completions."""

from postbridge.ai.schemas import GatewayUsageStats
from postbridge.ai.streaming import (
    extract_stream_delta_text,
    extract_usage_from_chunk,
    iter_openai_sse_json_payloads,
    root_total_tokens,
)


def test_extract_stream_delta_text_from_list_content() -> None:
    obj = {
        "choices": [
            {
                "delta": {
                    "content": [
                        {"type": "text", "text": "hel"},
                        {"type": "text", "text": "lo"},
                    ]
                }
            }
        ]
    }
    assert extract_stream_delta_text(obj) == "hel\nlo"


def test_extract_stream_delta_text_concatenates_chunks() -> None:
    parts: list[str] = []
    for obj in [
        {"choices": [{"delta": {"content": "hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {}}]},
    ]:
        f = extract_stream_delta_text(obj)
        if f:
            parts.append(f)
    assert "".join(parts) == "hello"


def test_extract_usage_and_root_total() -> None:
    u = extract_usage_from_chunk(
        {"choices": [], "usage": {"total_tokens": 15, "prompt_tokens": 10, "completion_tokens": 5}}
    )
    assert u is not None
    assert u.total_tokens == 15
    assert root_total_tokens({"total_tokens": 99}) == 99


def test_iter_openai_sse_json_payloads_skips_done_and_comments() -> None:
    lines = [
        ": ping",
        "",
        'data: {"choices":[{"delta":{"content":"x"}}]}',
        "data: [DONE]",
        'data: {"ignored":true}',
    ]
    out = list(iter_openai_sse_json_payloads(iter(lines)))
    assert len(out) == 1
    assert extract_stream_delta_text(out[0]) == "x"


def test_gateway_usage_stats_from_chunk() -> None:
    obj = {"usage": {"total_tokens": 3}}
    u = extract_usage_from_chunk(obj)
    assert isinstance(u, GatewayUsageStats)
    assert u.total_tokens == 3
