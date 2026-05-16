import { api } from './apiClient'
import { fetchAppJson, fetchRuntimeConfig, isSelfhostMode } from './runtime'
import { buildSelfhostWorkspace, SELFHOST_WORKSPACE_ID } from './workspace'

async function loadSelfhostUser() {
  const [runtime, session] = await Promise.all([
    fetchRuntimeConfig(),
    fetchAppJson('/session'),
  ])
  if (runtime?.app_mode !== 'selfhost') return null

  let currentSession = session
  if (!currentSession?.bootstrapped) {
    currentSession = await fetchAppJson('/bootstrap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_name: 'Postbridge Self-host' }),
    })
  }

  return {
    ...(currentSession?.user || { id: 'local-admin', display_name: 'Local Admin', role: 'admin' }),
    is_platform_admin: true,
    workspaces: [buildSelfhostWorkspace(currentSession?.tenant)],
    current_workspace_id: SELFHOST_WORKSPACE_ID,
    app_mode: 'selfhost',
    tenant: currentSession?.tenant,
    runtime,
    features: runtime?.features || {},
    capabilities: runtime?.capabilities || {},
  }
}

export function loadCurrentUser() {
  return isSelfhostMode() ? loadSelfhostUser() : api('/me')
}
