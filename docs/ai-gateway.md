# AI Gateway

Core talks to external AI providers through OpenAI-compatible HTTP APIs.

`AI_GATEWAY_BASE_URL` should be configured without a trailing slash. Examples:

- `https://api.openai.com/v1`
- `https://provider.example.com/v1`

## Request Language

Internal `generate` and `adapt` endpoints accept optional `target_language`. When it is not provided, Core uses `AI_GATEWAY_DEFAULT_RESPONSE_LANGUAGE` if configured. Empty values mean no default response language is requested.

## Usage Metadata

Core accepts several provider-specific usage aliases in responses for compatibility:

- `usage_tokens_charged`
- `gitsell_tokens_charged`
- `gitsell_tokens_spent`

New integrations should prefer the generic `usage_tokens_charged` field.
