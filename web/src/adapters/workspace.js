export const SELFHOST_WORKSPACE_ID = 'local'

export function buildSelfhostWorkspace(tenant) {
  return {
    id: SELFHOST_WORKSPACE_ID,
    name: tenant?.name || 'Postbridge Self-host',
    role: 'owner',
    can_manage_billing: true,
  }
}

export function workspaceEntryPath(user, explicitId = '') {
  const id = preferredWorkspaceId(user, explicitId)
  return id ? `/workspaces/${encodeURIComponent(id)}/content` : '/'
}

const preferenceKey = (user) => {
  const accountId = user?.user_id || user?.id
  return accountId ? `postbridge.workspace.${accountId}` : ''
}

export function preferredWorkspaceId(user, explicitId = '') {
  const workspaces = user?.workspaces || []
  if (workspaces.some((workspace) => workspace.id === explicitId)) return explicitId
  try {
    const stored = preferenceKey(user) && localStorage.getItem(preferenceKey(user))
    if (workspaces.some((workspace) => workspace.id === stored)) return stored
  } catch { /* Storage can be unavailable in private browsing. */ }
  return workspaces[0]?.id || ''
}

export function rememberWorkspace(user, workspaceId) {
  if (!preferenceKey(user) || !user?.workspaces?.some((workspace) => workspace.id === workspaceId)) return
  try { localStorage.setItem(preferenceKey(user), workspaceId) } catch { /* Optional preference. */ }
}

export function canManageWorkspaceBilling(user, workspaceId) {
  const workspace = user?.workspaces?.find((item) => item.id === workspaceId)
  return Boolean(workspace || user?.is_platform_admin)
}

// Object IDs and payment parameters belong to their original workspace.
export function workspaceSwitchPath(pathname, workspaceId) {
  const suffix = pathname.match(/^\/workspaces\/[^/]+\/(.*)/)?.[1] || ''
  let section = 'content'
  if (suffix.startsWith('settings')) section = 'settings'
  else if (suffix.startsWith('channels')) section = 'channels'
  else if (suffix.startsWith('content/videos')) section = 'content/videos'
  else if (suffix.startsWith('content/video-campaigns')) section = 'content/video-campaigns'
  else if (suffix.startsWith('agents/')) {
    const agent = suffix.split('/')[1]
    section = ['topic-scout', 'editor', 'help', 'ops', 'candidates', 'videos'].includes(agent)
      ? `agents/${agent}` : 'agents/topic-scout'
  }
  return `/workspaces/${encodeURIComponent(workspaceId)}/${section}`
}
