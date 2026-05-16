import { api } from './apiClient'

export function getWorkspaceSettings(workspaceId) {
  return api(`/workspaces/${workspaceId}/settings`)
}

export function updateWorkspaceSettings(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/settings`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}
