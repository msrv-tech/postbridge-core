import { api } from './apiClient'

export function listConnectors(workspaceId = '') {
  return api(workspaceId ? `/api/workspaces/${encodeURIComponent(workspaceId)}/connectors` : '/api/connectors')
}

export function getPublicExternalAppManifest(appId) {
  return api(`/api/external-apps/${encodeURIComponent(appId)}/manifest`)
}

export function listWorkspaceExternalApps(workspaceId) {
  return api(`/workspaces/${workspaceId}/external-apps`)
}

export function getWorkspaceExternalAppConnection(workspaceId, appId) {
  return api(`/workspaces/${workspaceId}/external-apps/${appId}/connection`)
}

export function listWorkspaceExternalAppInstallations(workspaceId) {
  return api(`/workspaces/${workspaceId}/external-apps/installations`)
}

export function createWorkspaceExternalAppInstallIntent(workspaceId, appId) {
  return api(`/workspaces/${workspaceId}/external-apps/${appId}/install-intent`, {
    method: 'POST',
  })
}

export function revokeWorkspaceExternalAppInstallation(workspaceId, installationId) {
  return api(`/workspaces/${workspaceId}/external-apps/installations/${installationId}`, {
    method: 'DELETE',
  })
}
