import { api } from './apiClient'

export function listContentItems(workspaceId, { status, limit = 20, offset = 0 } = {}) {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  return api(`/workspaces/${workspaceId}/posts?${params}`)
}

export function getContentItem(workspaceId, postId) {
  return api(`/workspaces/${workspaceId}/posts/${postId}`)
}

export function createContentItem(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/posts`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateContentItem(workspaceId, postId, payload) {
  return api(`/workspaces/${workspaceId}/posts/${postId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteContentItem(workspaceId, postId) {
  return api(`/workspaces/${workspaceId}/posts/${postId}`, { method: 'DELETE' })
}
