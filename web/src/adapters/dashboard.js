import { api } from './apiClient'
import { isSelfhostMode } from './runtime'

export function getDashboardSummary(workspaceId) {
  return api(`/workspaces/${workspaceId}/dashboard/summary`)
}

export function listDashboardJobs(workspaceId) {
  return api(`/workspaces/${workspaceId}/dashboard/jobs`)
}

export function getOnboardingState(workspaceId) {
  if (isSelfhostMode()) {
    return Promise.resolve(null)
  }
  return api(`/workspaces/${workspaceId}/onboarding/state`).catch((error) => {
    console.warn('Failed to load onboarding state', error)
    return null
  })
}
