import { api } from './apiClient'

export function getJob(workspaceId, jobId) {
  return api(`/workspaces/${workspaceId}/jobs/${jobId}`)
}

export function runJobAction(workspaceId, jobId, action) {
  return api(`/workspaces/${workspaceId}/jobs/${jobId}/${action}`, {
    method: 'POST',
  })
}

export function deleteJob(workspaceId, jobId) {
  return api(`/workspaces/${workspaceId}/jobs/${jobId}`, { method: 'DELETE' })
}

export function startHistoricalImportJob(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/jobs/start`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
