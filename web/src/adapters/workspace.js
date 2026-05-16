export const SELFHOST_WORKSPACE_ID = 'local'

export function buildSelfhostWorkspace(tenant) {
  return {
    id: SELFHOST_WORKSPACE_ID,
    name: tenant?.name || 'Postbridge Self-host',
  }
}

export function workspaceEntryPath(user) {
  const firstWorkspaceId = user?.workspaces?.[0]?.id
  return firstWorkspaceId ? `/workspaces/${firstWorkspaceId}/content` : '/'
}
