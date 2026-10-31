import { api } from './apiClient'

export function askSupportAssistant(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/assistant/help`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export const applyAssistantVideoAction = (workspaceId, cardId) => api(`/workspaces/${workspaceId}/assistant/actions/${cardId}/apply`, { method: 'POST' })

export const readAssistantAction = (workspaceId, cardId) => api(`/workspaces/${workspaceId}/assistant/actions/${cardId}`)

export const getAssistantExpenses = (workspaceId) => api(`/workspaces/${workspaceId}/assistant/expenses`)
