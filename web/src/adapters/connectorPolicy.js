import { normalizeConnectorId } from './connectorIntent.js'

export function projectConnectorCatalog(payload) {
  if (!['ru', 'io'].includes(payload?.deployment)) return []
  const items = Array.isArray(payload?.items) ? payload.items : []
  return items.filter(item => {
    if (!normalizeConnectorId(item?.id)) return false
    if (item.listed === false) return false
    if (!['data', 'mcp'].includes(item.kind)) return false
    if (!['available', 'pilot', 'planned'].includes(item.public_readiness)) return false
    return true
  }).sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))
}

export function connectorAllowsSetup(item) {
  return ['available', 'pilot'].includes(item?.public_readiness)
    && ['public', 'existing_installations', 'manual_setup'].includes(item?.rollout)
    && ['guide', 'manual_mcp'].includes(item?.setup_mode)
    && item?.next_action?.method === 'GET'
    && ['setup_guide', 'pilot_guide'].includes(item?.next_action?.kind)
}

export function connectorPersonalState(item) {
  const scope = item?.kind === 'mcp' ? 'user_client' : 'workspace'
  const value = item?.personal
  if (value?.scope !== scope || !['unknown', 'not_connected', 'connected', 'needs_login'].includes(value?.status)) return { scope, state: 'unknown' }
  return { scope, state: value.status, accountName: ['connected', 'needs_login'].includes(value.status) && typeof value.account_name === 'string' ? value.account_name : '' }
}

export function safeConnectorHref(value, { origin = window.location.origin, external = false } = {}) {
  if (typeof value !== 'string' || !value || /[\u0000-\u0020\u007f\\]/.test(value) || value.startsWith('//')) return null
  try {
    const url = new URL(value, origin)
    if (url.username || url.password) return null
    if (url.origin === origin && ['http:', 'https:'].includes(url.protocol)) return `${url.pathname}${url.search}${url.hash}`
    return external && url.protocol === 'https:' ? url.href : null
  } catch { return null }
}
