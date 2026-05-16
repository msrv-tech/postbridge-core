import { api } from './apiClient'

export function startMediaGenerationJob(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/media/generation-jobs`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getMediaGenerationJob(workspaceId, jobId) {
  return api(`/workspaces/${workspaceId}/media/generation-jobs/${jobId}`)
}

export function listMediaGenerationJobs(workspaceId, { limit = 10 } = {}) {
  const q = new URLSearchParams({ limit: String(limit) })
  return api(`/workspaces/${workspaceId}/media/generation-jobs?${q}`)
}

export function uploadWorkspaceMedia(workspaceId, file) {
  const formData = new FormData()
  formData.append('file', file)
  return api(`/workspaces/${workspaceId}/media/upload`, {
    method: 'POST',
    body: formData,
  })
}
