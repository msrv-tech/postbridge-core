import { api, getToken, setToken } from './apiClient'
import { fetchAppJson, fetchRuntimeConfig, isSelfhostMode } from './runtime'
import { buildSelfhostWorkspace, SELFHOST_WORKSPACE_ID } from './workspace'

function selfhostUserFromSession(session, runtime) {
  const user = session?.user || (session?.id ? session : null) || { id: 'local-admin', display_name: 'Local Admin', role: 'admin' }
  return {
    ...user,
    is_platform_admin: true,
    workspaces: Array.isArray(session?.workspaces) && session.workspaces.length
      ? session.workspaces
      : [buildSelfhostWorkspace(session?.tenant)],
    current_workspace_id: SELFHOST_WORKSPACE_ID,
    app_mode: 'selfhost',
    tenant: session?.tenant,
    runtime,
    features: runtime?.features || {},
    capabilities: runtime?.capabilities || {},
  }
}

async function tryAutoBootstrapSelfhost(runtime) {
  try {
    const result = await fetchAppJson('/bootstrap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_name: 'Postbridge Self-host' }),
    })
    if (!result?.authenticated || !result?.token) return null
    setToken(result.token)
    return selfhostUserFromSession(result, runtime)
  } catch (error) {
    console.warn('Unable to auto-bootstrap self-host session', error)
    return null
  }
}

async function loadSelfhostUser() {
  let runtime = null
  let session = null
  try {
    runtime = await fetchRuntimeConfig()
  } catch (error) {
    console.warn('Unable to load self-host runtime config', error)
    return null
  }
  if (runtime?.app_mode !== 'selfhost') return null

  try {
    session = await api('/me')
  } catch (error) {
    console.warn('Unable to load self-host session', error)
    return null
  }

  if (session?.__selfhost_auth_status) {
    if (session.setup_required && !getToken()) return tryAutoBootstrapSelfhost(runtime)
    return null
  }
  if (!session?.authenticated) return null

  return selfhostUserFromSession(session, runtime)
}

export function loadCurrentUser() {
  return isSelfhostMode()
    ? loadSelfhostUser()
    : api('/me').catch(() => null)
}
