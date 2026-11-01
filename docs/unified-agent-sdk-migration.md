# Unified agent migration to Agents SDK

Replace the editorial LangGraph runtime and the separate hosted assistant loops with one Postbridge agent definition and one OpenAI Agents SDK runner owned by Core. Each user, workspace, content item and scheduled occurrence retains its own execution context and history. A shared implementation never means shared conversation memory.

## Architecture

Core owns the SDK runner, editorial tools, durable content and video services, and self-host execution. Its portable `postbridge_agent_runtime` package is included directly in Core and distributed as a small wheel to hosted applications. SaaS supplies authenticated context, private integration tools, admission decisions and actual-usage accounting. Tools execute in the application process that owns their database session. Existing API routes remain compatibility adapters. Atomic MCP calls and media production remain ordinary service operations.

The model receives only the tools authorized for the current request. Server-side checks enforce resource ownership, current versions, limits, idempotency and publication consent. General support is workspace-free and platform-funded; generation dialogue remains workspace-funded. There are no specialist agents or handoffs.

## Implementation sequence

1. **Contracts and regression inventory.** Record existing editorial, research, support, commerce and video behavior. Define execution input, result, capability and usage contracts. Preserve existing API responses and historical records.
2. **Core SDK runtime.** Add the SDK dependency and one request-scoped agent runner with typed output, validated tools, bounded turns, timeouts, local events and provider usage capture. Do not wrap the old orchestrator as a tool.
3. **Editorial migration.** Extract source collection, context loading, deduplication, policy validation and candidate persistence as reusable operations. Replace graph execution for post editing and topic discovery, including source approval and follow-up. Route scheduled editorial tasks through the same runtime.
4. **Hosted integration.** Adapt the existing hosted tools and general support to the same Core-owned portable runtime. SaaS remains authoritative for user/workspace context, source access, action cards and billing. Bind tool callbacks to authenticated server-created request context; retain authentication for existing cross-service operations.
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
- One portable Core-owned agent definition and runner now serve the editor, topic discovery, scheduled editorial entry points, hosted workspace tools and general help. LangGraph, graph builders, the fallback interpreter and the separate hosted agent definition were removed.
- Shared execution has 50 focused SDK/editorial protocol, isolation, failure and receipt tests. The hosted package was verified without importing or installing the Core application.
- Full Core Docker CI passed: 813 tests, 78.72% total coverage, OpenAPI export succeeded.
- Full hosted test inventory ran in four Docker partitions with isolated PostgreSQL databases and Redis namespaces: 1129 passed, 1 skipped. Combined coverage and OpenAPI export succeeded. Line coverage: Core 82.86%, hosted 83.40%, product 83.13%; both 80% product gates passed.
- Production and E2E Dockerfile checks passed. Hosted CI, development setup and image builds consume the canonical Core runtime source.

## Release acceptance boundaries

Implementation and automated runtime migration checks are complete. A bounded live-provider/browser walkthrough remains a release check; no live paid generation or external publication was performed for this change.

The runtime migration retains the existing editorial scheduling and cross-service usage event contracts. Durable occurrence deduplication for broker redelivery and end-to-end reconciliation of unknown editorial calls remain operational hardening work; the generic SDK kernel does not provide these guarantees by itself. Known and unknown provider receipts are retained for that work.

## Core runtime contract

`postbridge_agent_runtime.run_postbridge_agent` is the shared SDK entry point. Core's `postbridge.agent.sdk_runtime` maps portable errors to existing domain errors. `AgentExecution` supplies the configured model, server-owned instructions, isolated history, typed tools, required tool names, output schema and execution limits. `AgentResult` returns the output, provider receipts and tool events. Existing SDK tools can retain their argument schemas and receive the current SDK tool context. The tested SDK version is pinned to `0.24.0`; its HTTP transport is `httpx2`.

An adapter supplies `before_provider_call` to authorize and durably record each attempt before dispatch, and `on_provider_receipt` to persist provider facts before the SDK parses the answer. GitSell charged units and raw provider tokens remain separate. An absent receipt is unknown usage, not zero cost. Dispatch retries are disabled. Tools execute on the request's event-loop thread to preserve ownership of synchronous database sessions.

The runner owns neither conversation storage nor billing tables. Adapters persist history, enforce idempotency, authorize resources and recover interrupted executions. Core editorial routes, workers, hosted workspace tools and general help use this implementation. Hosted build pipelines consume the canonical Core source checkout; deploy compatible Core source before building hosted images.

## References

- [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents/sdk)
- [Running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)
