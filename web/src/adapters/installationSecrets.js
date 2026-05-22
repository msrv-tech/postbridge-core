import { api } from './apiClient'

export function listInstallationSecrets(workspaceId) {
  return api(`/workspaces/${workspaceId}/installation-secrets`)
}

export function upsertInstallationSecret(workspaceId, category, payload) {
  return api(`/workspaces/${workspaceId}/installation-secrets/${category}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function startTelegramImportConnection(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/telegram-import/start`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function completeTelegramImportConnection(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/telegram-import/complete`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
