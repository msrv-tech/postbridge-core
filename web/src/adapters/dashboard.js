import { api } from './apiClient'

export function getDashboardSummary(workspaceId) {
  return api(`/workspaces/${workspaceId}/dashboard/summary`)
}

export function listDashboardJobs(workspaceId) {
  return api(`/workspaces/${workspaceId}/dashboard/jobs`)
}
