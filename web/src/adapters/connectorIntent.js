const INTENT_KEY = 'postbridge.connector_intent'
const INTENT_TTL_MS = 10 * 60 * 1000

export function normalizeConnectorId(value) {
  return typeof value === 'string' && value.length <= 80 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value) ? value : null
}

export function connectorResumePath(id) {
  const normalized = normalizeConnectorId(id)
  return normalized ? `/connectors/${normalized}?setup=1` : null
}

export function clearConnectorIntent() {
  try { window.sessionStorage.removeItem(INTENT_KEY) } catch { /* Optional continuity. */ }
}

export function rememberConnectorIntent(value) {
  const id = normalizeConnectorId(value)
  clearConnectorIntent()
  if (id) {
    try { window.sessionStorage.setItem(INTENT_KEY, JSON.stringify({ id, startedAt: Date.now() })) } catch { /* Optional continuity. */ }
  }
  return id
}

export function consumeConnectorIntent() {
  let value
  try { value = window.sessionStorage.getItem(INTENT_KEY) } catch { return null }
  clearConnectorIntent()
  try {
    const { id, startedAt } = JSON.parse(value)
    const elapsed = Date.now() - startedAt
    if (!Number.isFinite(startedAt) || elapsed < 0 || elapsed > INTENT_TTL_MS) return null
    return connectorResumePath(id)
  } catch { return null }
}
