# Agent runtime and editorial tools

Core owns the Postbridge agent definition and OpenAI Agents SDK runner in `postbridge.agent.sdk_runtime`. The editor, topic discovery API and scheduled editorial tasks use this implementation through `EditorialExecutionService`. Task modes such as `post_copilot` and `topic_scout` remain stable API and database identifiers; they do not select separate agent definitions.

## Editorial execution

The editorial adapter first reads the selected channel, style, recent publications and source evidence. Connector configuration is excluded from model context. Selecting a draft from another tenant fails before model dispatch. Source collection, shortlisting, image selection and source quality checks are deterministic operations in `postbridge.agent.operations`.

For a post, the SDK calls `submit_post` with the complete draft. Server acceptance checks retain source provenance, approved images, duplicate detection and platform constraints. The existing policy service decides whether the result becomes a review item or an editable draft. The tool cannot publish.

For topic discovery, the same SDK runner calls `submit_topics`, then `rank_topics` with the returned candidate indices. Zero or one candidate needs no paid ranking turn. Server validation controls candidate limits and ranking scope. Source quality, freshness, conflicts, topic alignment and angle diversity contribute to the final ordering. An invalid call can be corrected within the configured turn limit; accepted submissions cannot be replaced by a second submission.

Successful submission of a post or ranking of topics ends the SDK run immediately. A rejected tool call does not end the run. The runner serializes tool execution even if a compatible gateway ignores `parallel_tool_calls=false`.

## Source approval and history

When discovered sources require approval and the request contains no explicit source URLs, Core prepares the source review without calling a model. Approval resumes through the existing editor endpoint with the selected URLs and images. The existing content timeline, review records, candidates and scheduled task definitions remain available. Draft edits receive the current full draft and preserve the parts the user did not ask to change.

## Provider attempts and receipts

Before each SDK model call, Core commits a pending `sdk_provider_call` step. The response hook stores the provider receipt before SDK decoding, including failed responses. A lost response remains unknown. A later SDK or tool failure preserves those records and marks the run failed. Generation requests are not automatically replayed.

`AGENT_LLM_USAGE_FORMAT=gitsell` interprets the gateway's charged units separately from raw provider tokens. Hosted Core defaults to this format; self-host defaults to `provider_tokens`. The existing `token_usage.total_tokens` API field remains a compatibility total in the configured unit system. Each receipt retains its explicit currency/token fields and usage format. Missing cost is not represented as a zero-cost receipt; `usage_pending` marks successful runs needing reconciliation.

The hosted assistant, general support and cross-service billing integration are subsequent steps of the [migration plan](unified-agent-sdk-migration.md). Media production, embeddings and direct MCP operations remain service operations, not additional agent runtimes.

## Configuration

| Variable | Purpose |
| --- | --- |
| `AGENT_LLM_BASE_URL`, `AGENT_LLM_API_KEY`, `AGENT_LLM_DEFAULT_MODEL` | Existing gateway/provider settings, with the existing `AI_GATEWAY_*` fallbacks |
| `AGENT_SDK_MAX_TURNS` | Maximum SDK model turns, default 6 |
| `AGENT_SDK_MAX_OUTPUT_TOKENS` | Maximum output tokens per model turn; falls back to explicitly configured `AGENT_LLM_MAX_TOKENS`, otherwise 8192 |
| `AGENT_LLM_USAGE_FORMAT` | `gitsell` or `provider_tokens`; configure explicitly for other compatible gateways |

The same API and worker images contain the SDK runtime. There is no LangGraph dependency, graph compiler, fallback graph interpreter or separate editorial agent loop.
