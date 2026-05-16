import { api } from './apiClient'

export function listAdminWorkspaces() {
  return api('/admin/workspaces')
}
