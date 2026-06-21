import { api } from './apiClient'

export function askSupportAssistant(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/assistant/help`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

