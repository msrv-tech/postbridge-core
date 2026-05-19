import { api } from './apiClient'

export function listInstallationSecrets(workspaceId) {
  return api(`/workspaces/${workspaceId}/installation-secrets`)
}
