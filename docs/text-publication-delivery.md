# Text publication delivery evidence

The shared publication-target executor commits a per-attempt checkpoint before
calling a text publisher. A transport error, unexpected exception, missing
acknowledgement identifier, or worker restart after dispatch does not prove that
the destination rejected the post. These targets become `unknown` and require
reconciliation. `retryable` and `failed` alone never authorize a resend.

Checkpoints use the existing Core PostgreSQL `video_delivery_states` table,
keyed by publication target. The legacy `encrypted_state` text column stores
tagged JSON for text receipts: only `kind=text_publication_v1`, an attempt ID,
outcome and optional error code. It contains no payload, credentials or provider
URLs and requires no encryption key. Approved video publications retain
their existing provider checkpoints and delivery state machine. No schema,
additional volume, bucket, queue, or backup service is introduced.

## Boundaries and retries

- Claim and a `not_sent` receipt commit together. Preparation failures before
  entering a publisher remain failed with positive `not_sent` evidence.
- Immediately before entering a publisher, `dispatching` commits. A crash from
  this boundary onwards is ambiguous, even if it happened just before the
  network call. This intentionally favors reconciliation over duplication.
- A returned nonempty external identifier is persisted with the published target
  and a `sent` receipt. Core does not invent an identifier or successful delivery.
- Explicit trusted adapter `publication_outcome=not_sent/rejected` evidence
  remains a known refusal. A bare HTTP status, timeout, retry hint, or generic
  non-retryable exception is not sufficient evidence: a multi-step publisher
  may already have created a post before the failing step. Adapters lacking
  specific proof fail closed, including ordinary 4xx errors.
- Automatic retry requires both a failed target with durable positive evidence
  and a retryable error within the existing retry limit. Manual dispatch also
  requires positive evidence before requeueing a failed target. Confirmed
  successes remain idempotent.
- A stale claim recovers to pending only if its receipt still proves preparation
  never reached dispatch. Attempt IDs prevent the old worker from dispatching
  after recovery or a later claim. Stale dispatch and legacy publishing rows
  without proof become unknown.
- Live-sync and import dedup claims are retained for ambiguous outcomes, so
  ingestion or import retry cannot silently create another delivery. Import
  reconciliation records unknown targets as failures, not successful delivery.

## API evidence

Self-host target views, internal service target views, and target status outbox
events include:

| Field | Meaning |
| --- | --- |
| `delivery_outcome` | `not_started`, `not_sent`, `rejected`, `sent`, or `unknown` |
| `reconciliation_required` | True when delivery cannot be established safely |
| `delivery_retry_safe` | True only for persisted `not_sent`/`rejected` evidence |

`not_started` is the initial pending state, not a historical refusal receipt.
Legacy failed targets without evidence report unknown. Consumers must read the
evidence rather than infer non-delivery from `failed`. Manual dispatch rejects
ambiguous targets with `VALIDATION_PUBLICATION_RECONCILIATION_REQUIRED`; queuing
another worker message cannot bypass the durable claim guard. Tenant and other
existing API authorization checks run first.

Core performs no automatic destination lookup, acknowledgement repair, or
operator override in this change. Historical failures may therefore need manual
reconciliation even if an operator believes they were never sent. Direct legacy
publisher paths outside the publication-target executor are not upgraded by this
contract. Approved-video polling, retry, and reconciliation remain independent.

## Backup and recovery

Receipts belong to the existing Core PostgreSQL database. Include the checkpoint
table in the same consistent backup as publication targets and the rest of the
database. Restore the complete agreed database snapshot; do not restore receipts
separately from their publication targets. Use the deployment's existing backup
and recovery process rather than introducing another receipt-specific service.

An external publication cannot be rolled back by restoring PostgreSQL. Keep
delivery workers and dispatch paused during restoration, and reconcile possible
external sends since the restored dump before resuming them. Do not erase an
unknown receipt or reset it to pending to manufacture proof of non-delivery.
