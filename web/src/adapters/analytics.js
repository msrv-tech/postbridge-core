import { api } from './apiClient'

function query(filters) {
  return new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== '' && value != null))
}

export function publicationAnalytics(workspaceId, filters = {}) {
  return api(`/workspaces/${encodeURIComponent(workspaceId)}/analytics/publications?${query(filters)}`)
}

export function newsAnalytics(workspaceId, days) {
  return api(`/workspaces/${encodeURIComponent(workspaceId)}/analytics/news?${query({ days })}`)
}
