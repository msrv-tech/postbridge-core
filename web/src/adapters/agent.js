import { api } from './apiClient'

export function listAgentReviewQueue(workspaceId) {
  return api(`/workspaces/${workspaceId}/agent/review-queue`)
}

export function getAgentReviewItem(workspaceId, reviewItemId) {
  return api(`/workspaces/${workspaceId}/agent/review-queue/${reviewItemId}`)
}

export function resolveAgentReviewItem(workspaceId, reviewItemId, payload) {
  return api(`/workspaces/${workspaceId}/agent/review-queue/${reviewItemId}/resolve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listAgentRuns(workspaceId) {
  return api(`/workspaces/${workspaceId}/agent/runs`)
}

export function getAgentRun(workspaceId, runId) {
  return api(`/workspaces/${workspaceId}/agent/runs/${runId}`)
}

export function listAgentRunSteps(workspaceId, runId) {
  return api(`/workspaces/${workspaceId}/agent/runs/${runId}/steps`)
}

export function createAgentRun(workspaceId, payload, options = {}) {
  return api(`/workspaces/${workspaceId}/agent/runs`, {
    method: 'POST',
    body: JSON.stringify(payload),
    ...options,
  })
}

export function getAgentEditorTimeline(workspaceId, contentItemId) {
  return api(`/workspaces/${workspaceId}/agent/content-items/${contentItemId}/timeline`)
}

export function createAgentEditorMessage(workspaceId, contentItemId, payload, options = {}) {
  return api(`/workspaces/${workspaceId}/agent/content-items/${contentItemId}/messages`, {
    method: 'POST',
    body: JSON.stringify(payload),
    ...options,
  })
}

export function getPostPlatformPreviews(workspaceId, payload, options = {}) {
  return api(`/workspaces/${workspaceId}/posts/platform-previews`, {
    method: 'POST',
    body: JSON.stringify(payload),
    ...options,
  })
}

export function getAgentCandidate(workspaceId, candidateId) {
  return api(`/workspaces/${workspaceId}/agent/candidates/${candidateId}`)
}

export function listAgentTasks(workspaceId) {
  return api(`/workspaces/${workspaceId}/agent/tasks`)
}

export function createAgentTask(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/agent/tasks`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function pauseAgentTask(workspaceId, taskId) {
  return api(`/workspaces/${workspaceId}/agent/tasks/${taskId}/pause`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export function resumeAgentTask(workspaceId, taskId) {
  return api(`/workspaces/${workspaceId}/agent/tasks/${taskId}/resume`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export function deleteAgentTask(workspaceId, taskId) {
  return api(`/workspaces/${workspaceId}/agent/tasks/${taskId}`, {
    method: 'DELETE',
  })
}

export function runAgentTask(workspaceId, taskId) {
  return api(`/workspaces/${workspaceId}/agent/tasks/${taskId}/run`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export function getAgentAnalyticsOverview(workspaceId) {
  return api(`/workspaces/${workspaceId}/agent/analytics/overview`)
}

export function getAgentAnalyticsTimeseries(workspaceId, { days } = {}) {
  const q = new URLSearchParams()
  if (days != null) q.set('days', String(days))
  return api(`/workspaces/${workspaceId}/agent/analytics/timeseries${q.toString() ? `?${q}` : ''}`)
}

export function getAgentAnalyticsQuality(workspaceId, { days } = {}) {
  const q = new URLSearchParams()
  if (days != null) q.set('days', String(days))
  return api(`/workspaces/${workspaceId}/agent/analytics/quality${q.toString() ? `?${q}` : ''}`)
}

export function listAgentPolicies(workspaceId) {
  return api(`/workspaces/${workspaceId}/agent/policies`)
}

export function upsertAgentPolicy(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/agent/policies`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function getWorkspaceAgentPolicy(workspaceId) {
  return api(`/workspaces/${workspaceId}/agent/workspace-policy`)
}

export function upsertWorkspaceAgentPolicy(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/agent/workspace-policy`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function getAgentEmbeddingsLifecycle(workspaceId, { channelId, channelLimit, channelOffset } = {}) {
  const q = new URLSearchParams()
  if (channelId) q.set('channel_id', channelId)
  if (channelLimit != null) q.set('channel_limit', String(channelLimit))
  if (channelOffset != null) q.set('channel_offset', String(channelOffset))
  return api(`/workspaces/${workspaceId}/agent/embeddings/lifecycle${q.toString() ? `?${q}` : ''}`)
}

export function reindexAgentChannelEmbeddings(workspaceId, channelId, payload) {
  return api(`/workspaces/${workspaceId}/agent/reindex/channel/${channelId}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function rotateAgentChannelEmbeddings(workspaceId, channelId, payload) {
  return api(`/workspaces/${workspaceId}/agent/reindex/channel/${channelId}/rotate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function reindexAgentEmbeddingDrift(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/agent/reindex/drift`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function maintainAgentEmbeddings(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/agent/embeddings/maintenance`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function compactAgentEmbeddings(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/agent/embeddings/compact`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function cleanupAgentRuntime(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/agent/cleanup`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
