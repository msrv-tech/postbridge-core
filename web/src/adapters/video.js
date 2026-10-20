import { api } from './apiClient'

const path = (workspaceId, suffix = '') => `/workspaces/${workspaceId}/videos${suffix}`
export const getVideoCapabilities = (workspaceId) => api(path(workspaceId, '/capabilities'))
export const listVideos = (workspaceId, offset = 0) => api(path(workspaceId, `?offset=${offset}&limit=20`))
export const getVideo = (workspaceId, id) => api(path(workspaceId, `/${id}`))
export const createVideo = (workspaceId, payload) => api(path(workspaceId), { method: 'POST', body: JSON.stringify(payload) })
export const reviseVideo = (workspaceId, id, payload) => api(path(workspaceId, `/${id}/revisions`), { method: 'POST', body: JSON.stringify(payload) })
export const createVideoVoiceVariant = (workspaceId, id, payload) => api(path(workspaceId, `/${id}/voice-variants`), { method: 'POST', body: JSON.stringify(payload) })
export const cancelVideo = (workspaceId, id, version, sourceVersion) => api(path(workspaceId, `/${id}/cancel`), { method: 'POST', body: JSON.stringify({ expected_version: version, ...(sourceVersion ? { source_version: sourceVersion } : {}) }) })
export const prepareVideoPost = (workspaceId, id, payload) => api(path(workspaceId, `/${id}/post-draft`), { method: 'POST', body: JSON.stringify(payload) })
export const getVideoPublication = (workspaceId, id) => api(path(workspaceId, `/posts/${id}/publication`))
export const approveVideoPublication = (workspaceId, id, payload) => api(path(workspaceId, `/posts/${id}/publication/approve`), { method: 'POST', body: JSON.stringify(payload) })
export const retryVideoPublication = (workspaceId, id, targetId) => api(path(workspaceId, `/posts/${id}/publication/targets/${targetId}/retry`), { method: 'POST' })
export const cancelVideoPublication = (workspaceId, id) => api(path(workspaceId, `/posts/${id}/publication/cancel`), { method: 'POST' })
export const rescheduleVideoPublication = (workspaceId, id, payload) => api(path(workspaceId, `/posts/${id}/publication/reschedule`), { method: 'POST', body: JSON.stringify(payload) })
export function uploadVideoMaterial(workspaceId, file) {
  const body = new FormData()
  body.append('file', file)
  return api(path(workspaceId, '/media/upload'), { method: 'POST', body })
}
export const listVideoMaterials = (workspaceId, { search = '', folder, offset = 0 } = {}) => {
  const query = new URLSearchParams({ search, offset: String(offset), limit: '20' })
  if (folder !== undefined) query.set('folder', folder)
  return api(path(workspaceId, `/materials?${query}`))
}
export const updateVideoMaterial = (workspaceId, id, payload) => api(path(workspaceId, `/materials/${id}`), { method: 'PATCH', body: JSON.stringify(payload) })

export const reconcileVideoPublication = (workspaceId, id, targetId) => api(path(workspaceId, `/posts/${id}/publication/targets/${targetId}/reconcile`), { method: 'POST' })
