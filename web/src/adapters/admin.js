import { api, getToken } from './apiClient'
import { fetchAppJson, isSelfhostMode } from './runtime'
import { buildSelfhostWorkspace } from './workspace'

export async function listAdminWorkspaces() {
  if (isSelfhostMode()) {
    let session = null
    try {
      session = await fetchAppJson('/session', {
        headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
      })
    } catch {
      return { items: [] }
    }
    return {
      items: session?.tenant ? [buildSelfhostWorkspace(session.tenant)] : [],
    }
  }
  return api('/admin/workspaces')
}
