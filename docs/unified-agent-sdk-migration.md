# Unified agent migration to Agents SDK

Replace the editorial LangGraph runtime and the separate hosted assistant loops with one Postbridge agent definition and one OpenAI Agents SDK runner owned by Core. Each user, workspace, content item and scheduled occurrence retains its own execution context and history. A shared implementation never means shared conversation memory.

## Architecture

Core owns the SDK runner, editorial tools, durable content and video services, and self-host execution. SaaS supplies authenticated context, private integration tools, admission decisions and actual-usage accounting. Existing API routes remain compatibility adapters. Atomic MCP calls and media production remain ordinary service operations.

The model receives only the tools authorized for the current request. Server-side checks enforce resource ownership, current versions, limits, idempotency and publication consent. General support is workspace-free and platform-funded; generation dialogue remains workspace-funded. There are no specialist agents or handoffs.

## Implementation sequence

1. **Contracts and regression inventory.** Record existing editorial, research, support, commerce and video behavior. Define execution input, result, capability and usage contracts. Preserve existing API responses and historical records.
2. **Core SDK runtime.** Add the SDK dependency and one request-scoped agent runner with typed output, validated tools, bounded turns, timeouts, local events and provider usage capture. Do not wrap the old orchestrator as a tool.
3. **Editorial migration.** Extract source collection, context loading, deduplication, policy validation and candidate persistence as reusable operations. Replace graph execution for post editing and topic discovery, including source approval and follow-up. Route scheduled editorial tasks through the same runtime.
4. **Hosted integration.** Adapt the existing hosted tools and general support to Core execution. SaaS remains authoritative for user/workspace context, source access, action cards and billing. Authenticate internal tool calls and bind them to a server-created execution.
5. **Execution records and accounting.** Correlate runs, messages, calls, action cards and jobs. Persist per-provider-call actual usage before SDK parsing. Preserve unknown outcomes for reconciliation, prevent duplicate actions and charges, and retain support caps. Cancellation must stop subsequent steps without repeating paid calls.
6. **Entry points.** Switch assistant, editor, topic discovery and scheduled invocations. Keep direct MCP operations deterministic and backed by the same services. Reuse existing frontend routes and review-card behavior.
7. **Acceptance and removal.** Run targeted tests, full Docker CI and browser acceptance. Remove LangGraph, SimpleCompiledGraph, graph builders, duplicate agent definitions and legacy runtime flags after replacement coverage passes. Do not delete historical records.

## Acceptance matrix

| Scenario | Required behavior |
| --- | --- |
| Post creation and editing | Full draft result; minimal requested edits; preserve unrelated text and approved images; follow-up history |
| Research | Real sources, freshness/domain filters, source approval before drafting when required, deduplication and diverse candidates |
| Scheduled editorial tasks | Durable occurrence, correct actor and workspace, one result and one charge under redelivery |
| Video and commerce | Existing tools and review cards, owned materials and exact revisions, separate publication approval |
| General support | No workspace tools or private context, bounded platform allowance |
| Failures | Retain saved results and known expenses; unknown paid outcomes are reconciled rather than replayed |
| Isolation | No cross-user/workspace history, resources, credentials or permissions |
| Deployment modes | Hosted admission and callbacks; self-host without private SaaS dependencies |

## Verification

Use the repository Docker Compose CI environment. Exercise actual SDK tool calling with a local OpenAI-compatible fake endpoint, alongside existing domain/API regressions. Test provider errors, malformed tool arguments, turn limits, interrupted responses, concurrent calls, stale versions, duplicate requests and billing receipts. Run browser checks for editor follow-up, source review and assistant cards. Live paid acceptance requires a bounded configured test environment; no external publication is needed for migration acceptance.

## Progress

- Plan recorded on 1 November 2026 against Core `b751aa8` and SaaS `d0cac28`.
- Shared Core SDK kernel implemented and verified: 29 SDK protocol/failure tests and 134 existing editorial/provider/retrieval/AI regressions passed in Docker CI.
- Next: migrate editorial execution and hosted adapters; production routes still use their existing implementations.

## Core runtime contract

`postbridge.agent.sdk_runtime.run_postbridge_agent` is the shared SDK entry point. `AgentExecution` supplies the configured model, server-owned instructions, isolated history, typed tools, required tool names, output schema and execution limits. `AgentResult` returns the output, provider receipts and tool events. The tested SDK version is pinned to `0.24.0`; its HTTP transport is `httpx2`.

An adapter supplies `before_provider_call` to authorize and durably record each attempt before dispatch, and `on_provider_receipt` to persist provider facts before the SDK parses the answer. GitSell charged units and raw provider tokens remain separate. An absent receipt is unknown usage, not zero cost. Dispatch retries are disabled. Tools execute on the request's event-loop thread to preserve ownership of synchronous database sessions.

The runner owns neither conversation storage nor billing tables. Adapters must persist history, enforce idempotency, authorize resources and recover interrupted executions. The initial kernel does not switch existing production routes; editorial and hosted adapter migration remain subsequent steps.

## References

- [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents/sdk)
- [Running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)
