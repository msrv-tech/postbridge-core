import { api } from './apiClient'

export function listBridges(workspaceId) {
  return api(`/workspaces/${workspaceId}/bridges`)
}

export function deleteBridge(workspaceId, bridgeId) {
  return api(`/workspaces/${workspaceId}/bridges/${bridgeId}`, { method: 'DELETE' })
}

export function updateBridge(workspaceId, bridgeId, payload) {
  return api(`/workspaces/${workspaceId}/bridges/${bridgeId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}
