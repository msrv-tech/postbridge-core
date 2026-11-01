"""Model boundary for existing domain regressions.

These tests keep their established model fixtures; real SDK protocol behavior is
covered separately in test_editorial_sdk and test_agent_sdk_runtime.
"""
import inspect
from uuid import uuid4

from postbridge.agent.editorial import PostSubmission, TopicSubmission, TopicRanking
from postbridge.agent.sdk_runtime import AgentResult, ProviderReceipt, ToolEvent
from postbridge.config import get_settings


async def fake_editorial_run(self, *, messages, tools, required, terminal, finish_when, on_start, on_receipt):
    receipts, events = [], []

    async def notify(callback, value):
        if callback:
            result = callback(value)
            if inspect.isawaitable(result):
                await result

    async def record(usage, call_id):
        gitsell = get_settings().agent_llm_usage_format == "gitsell"
        receipt = ProviderReceipt(call_id=call_id, status="succeeded",
            charged_units=usage.get("total_tokens") if gitsell else None,
            total_tokens=usage.get("total_tokens") if not gitsell else None,
            input_tokens=usage.get("prompt_tokens") if not gitsell else None,
            output_tokens=usage.get("completion_tokens") if not gitsell else None)
        receipts.append(receipt)
        await notify(on_receipt, receipt)

    call_id = uuid4().hex
    await notify(on_start, call_id)
    payload, usage = self.provider.invoke_json(messages=messages)
    await record(usage, call_id)
    if self.mode == "post_copilot":
        tools[0].execute(PostSubmission(candidate=payload))
        events.append(ToolEvent(tool_name="submit_post", status="succeeded"))
    else:
        submission = tools[0].execute(TopicSubmission(candidates=payload.get("candidates", [payload])))
        events.append(ToolEvent(tool_name="submit_topics", status="succeeded"))
        if not submission["ranking_needed"]:
            return AgentResult(output="Accepted", model=getattr(self.provider, "model_name", "test-model"),
                receipts=receipts, tool_events=events)
        candidates = submission["candidates"]
        call_id = uuid4().hex
        await notify(on_start, call_id)
        ranked, usage = self.provider.invoke_rerank(query=messages[-1]["content"], items=candidates, top_k=len(candidates))
        await record(usage, call_id)
        # Older generic fixtures included out-of-range indices for one result.
        ranked = [item for item in ranked if 0 <= item["index"] < len(candidates)]
        tools[1].execute(TopicRanking(ranked=ranked))
        events.append(ToolEvent(tool_name="rank_topics", status="succeeded"))
    return AgentResult(output="Accepted", model=getattr(self.provider, "model_name", "test-model"),
        receipts=receipts, tool_events=events)
