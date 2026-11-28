import { api } from './apiClient'
import { safeConnectorHref } from './connectorPolicy'

export const WB = 'wildberries-source'
export const MARKET = 'yandex-market-source'
export const pilotNames = { [WB]: 'Wildberries', [MARKET]: 'Yandex Market' }

const safeId = (value) => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(value)

export function projectCommercePilots(payload, workspaceId) {
  if (!workspaceId || payload?.workspace_id !== workspaceId || !Array.isArray(payload.private_pilots)) return []
  return [WB, MARKET].flatMap(id => {
    const matching = payload.private_pilots.filter(entry => entry?.id === id)
    const entry = matching[0]
    if (matching.length !== 1 || typeof entry.can_connect !== 'boolean' || !Array.isArray(entry.connection_ids)
      || !entry.connection_ids.every(safeId)) return []
    const connections = [...new Set(entry.connection_ids)]
    return entry.can_connect || connections.length ? [{ id, canConnect: entry.can_connect, connections }] : []
  })
}

function pilotRequest(path, { method = 'GET', body, signal } = {}) {
  return api(path, { method, signal, cache: 'no-store', ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
}

export function commercePilotApi(workspaceId, provider) {
  const workspace = encodeURIComponent(workspaceId)
  const wb = provider === WB
  const base = wb ? `/workspaces/${workspace}/wildberries/connections`
    : `/api/workspaces/${workspace}/connectors/yandex-market/connections`
  const connection = id => `${base}/${encodeURIComponent(id)}`
  const product = (id, article) => `${connection(id)}/products/${encodeURIComponent(article)}`
  return {
    connect: (token, campaign) => pilotRequest(base, { method: 'POST', body: wb ? { token } : { api_key: token, campaign_id: campaign } }),
    status: (id, signal) => pilotRequest(connection(id), { signal }),
    revoke: (id, version) => pilotRequest(connection(id) + (wb ? `?version=${encodeURIComponent(version)}` : ''), { method: 'DELETE' }),
    sizes: (id, article) => pilotRequest(`${product(id, article)}/sizes`),
    preview: (id, selection) => pilotRequest(wb
      ? `${product(id, selection.id)}?size_id=${selection.size}`
      : `${connection(id)}/offer?${new URLSearchParams({ offer_id: selection.id })}`),
    prepare: (id, selection) => pilotRequest(`${product(id, selection.id)}/draft-review`,
      { method: 'POST', body: { size_id: selection.size } }),
    confirm: (id, operation) => wb
      ? pilotRequest(`${connection(id)}/draft-reviews/${encodeURIComponent(operation.cardId)}/apply`, { method: 'POST' })
      : pilotRequest(`${connection(id)}/drafts`, { method: 'POST',
        body: { offer_id: operation.selection.id, idempotency_key: operation.requestKey } }),
  }
}

function invalid() { throw new Error('Invalid commerce pilot response') }

export function readPilotConnection(value, expectedId, wb) {
  if (!safeId(value?.id) || (expectedId && value.id !== expectedId)
    || !['connected', 'revoked', 'reauth_required'].includes(value.status)
    || (wb && (!Number.isSafeInteger(value.version) || value.version < 1))) invalid()
  return value
}

export function readPilotSizes(value) {
  if (!Array.isArray(value?.items) || !value.items.every(row =>
    Number.isSafeInteger(row?.size_id) && row.size_id > 0 && typeof row.label === 'string')) invalid()
  return [...new Map(value.items.map(row => [row.size_id, row])).values()]
}

function price(value) {
  return typeof value?.amount === 'string' && /^\d+(?:\.\d+)?$/.test(value.amount)
    && typeof value.currency === 'string' && /^[A-Z]{3}$/.test(value.currency)
    ? `${value.amount} ${value.currency}` : null
}

export function readPilotProduct(value, selection, wb) {
  const item = wb ? value?.product : value
  if ((wb ? item?.product_id : item?.id) !== selection.id || (wb && value?.size_id !== selection.size)
    || typeof (wb ? item?.title : item?.name) !== 'string') invalid()
  const prices = wb ? (value.price_status === 'reported' ? [price(item.price)] : []) : (item.prices || []).map(price)
  return { title: wb ? item.title : item.name, description: item.description, prices: prices.filter(Boolean) }
}

export function readPilotReview(value, selection) {
  const { card, source } = value || {}
  const verifiedPrice = source?.price_status === 'reported' ? price(source.price) : null
  if (source?.article !== selection.id || source.size_id !== selection.size
    || typeof card?.id !== 'string' || !/^\d+$/.test(card.id) || card.status !== 'proposed'
    || typeof card.summary?.text !== 'string' || !card.summary.text.trim()) invalid()
  return { selection, cardId: card.id, summary: card.summary.text, price: verifiedPrice }
}

export function readPilotDraft(result, workspaceId) {
  const id = result?.post_reference?.id
  if (!safeId(id) || result.content_result?.status !== 'draft') invalid()
  return pilotDraftRoute({ id, route: result.route }, workspaceId) || invalid()
}

export function pilotDraftRoute(draft, workspaceId) {
  if (!safeId(draft?.id)) return null
  const expected = `/workspaces/${encodeURIComponent(workspaceId)}/content/${encodeURIComponent(draft.id)}`
  return safeConnectorHref(draft.route) === expected ? { id: draft.id, route: expected } : null
}
