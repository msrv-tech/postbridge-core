import { isCapabilityEnabled } from './featureFlags'
import { api } from './apiClient'

export function isBillingEnabled(user) {
  return isCapabilityEnabled(user, 'billing', true)
}

export function listBillingPlans(workspaceId) {
  return api(`/workspaces/${workspaceId}/billing/plans`)
}

export function requestBillingEmail(payload) {
  return api('/me/billing-email/request', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function verifyBillingEmail(payload) {
  return api('/me/billing-email/verify', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createSubscription(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/billing/subscription/create`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function cancelSubscription(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/billing/subscription/cancel`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function initMigrationPayment(workspaceId, { provider, bridgeId }) {
  const q = new URLSearchParams({
    provider,
    bridge_id: bridgeId,
  })
  return api(`/workspaces/${workspaceId}/billing/migration/init-payment?${q}`, {
    method: 'POST',
  })
}
