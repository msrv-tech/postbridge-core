import { api } from './apiClient'

export function listPublicationTargetProjections(workspaceId, { limit = 50, offset = 0 } = {}) {
  const q = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return api(`/workspaces/${workspaceId}/core-publication-targets?${q}`)
}

export function dispatchPublicationTarget(workspaceId, targetId) {
  return api(`/workspaces/${workspaceId}/core-publication-targets/${targetId}/dispatch`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}
