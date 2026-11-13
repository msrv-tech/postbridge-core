import { api, isSelfhostMode } from './apiClient'

const inventoryFields = ['id', 'platform', 'platform_channel_id', 'title', 'state', 'usable']
const exactFields = (value, fields) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === fields.length && fields.every(key => Object.hasOwn(value, key))
const boundedString = (value, max, min = 0) => typeof value === 'string'
  && value.length <= max * 2 && [...value].length >= min && [...value].length <= max
const invalidInventory = () => Object.assign(new Error('Invalid owner inventory response'), { code: 'META_OWNER_INVENTORY_INVALID' })

export function parseMetaOwnerInventory(value) {
  if (!exactFields(value, ['schema_version', 'scope', 'items', 'next_cursor'])
    || value.schema_version !== 1 || value.scope !== 'owner_inventory'
    || !Array.isArray(value.items) || value.items.length > 100
    || value.next_cursor !== null && !boundedString(value.next_cursor, 1024)) throw invalidInventory()
  const ids = new Set()
  for (const item of value.items) {
    if (!exactFields(item, inventoryFields) || !boundedString(item.id, 64, 1)
      || !['facebook', 'instagram'].includes(item.platform) || !boundedString(item.platform_channel_id, 255)
      || item.title !== null && !boundedString(item.title, 255)
      || !['pending', 'reconnect_required', 'saved'].includes(item.state) || item.usable !== false
      || ids.has(item.id)) throw invalidInventory()
    ids.add(item.id)
  }
  return value
}

export async function listMyMetaConnections({ cursor = null, signal } = {}) {
  if (isSelfhostMode()) throw invalidInventory()
  if (cursor !== null && !boundedString(cursor, 1024)) throw invalidInventory()
  const query = new URLSearchParams({ limit: '50' })
  if (cursor !== null) query.set('cursor', cursor)
  return parseMetaOwnerInventory(await api(`/me/meta-connections?${query}`, { signal, cache: 'no-store' }))
}

export function listChannelRegistry(workspaceId) {
  return api(`/workspaces/${workspaceId}/channel-registry`)
}

export function listWorkspaceChannels(workspaceId) {
  return api(`/workspaces/${workspaceId}/channels`)
}

export function getChannelRegistryItem(workspaceId, channelId) {
  return api(`/workspaces/${workspaceId}/channel-registry/${channelId}`)
}

export function createChannelRegistryItem(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/channel-registry`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateChannelRegistryItem(workspaceId, channelId, payload) {
  return api(`/workspaces/${workspaceId}/channel-registry/${channelId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteChannelRegistryItem(workspaceId, channelId) {
  return api(`/workspaces/${workspaceId}/channel-registry/${channelId}`, { method: 'DELETE' })
}

export function validateChannelRegistryItem(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/channel-registry/validate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function requestMaxChannelVerification(workspaceId, platformChannelId) {
  return api(`/workspaces/${workspaceId}/channel-registry/max/request-verification`, {
    method: 'POST',
    body: JSON.stringify({ platform_channel_id: platformChannelId }),
  })
}

export function verifyMaxChannel(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/channel-registry/max/verify`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createVkCommunityCredential(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/credentials/vk/community-token`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getLinkedinAuthorizeUrl(workspaceId) {
  return api(`/workspaces/${workspaceId}/credentials/linkedin/authorize-url`)
}

export function getXAuthorizeUrl(workspaceId) {
  return api(`/workspaces/${workspaceId}/credentials/x/authorize-url`)
}

export function getMetaAuthorizeUrl(workspaceId) {
  return api(`/workspaces/${workspaceId}/credentials/meta/authorize-url`)
}

export const getYouTubeAuthorizeUrl = (workspaceId, acceptedPolicy = false) => api(`/workspaces/${workspaceId}/credentials/youtube/authorize-url?accepted_policy=${acceptedPolicy ? 'true' : 'false'}`)
export const getTikTokAuthorizeUrl = (workspaceId, acceptedPolicy = false) => api(`/workspaces/${workspaceId}/credentials/tiktok/authorize-url?accepted_policy=${acceptedPolicy ? 'true' : 'false'}`)

export function listLinkedinOrganizations(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/credentials/linkedin/organizations`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createLinkedinAccessTokenCredential(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/credentials/linkedin/access-token`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createManualPlatformCredential(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/credentials/platform/manual`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function validateManualPlatformCredential(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/credentials/platform/validate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listMetaPages(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/credentials/meta/pages`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createConnection(workspaceId, payload) {
  return api(`/workspaces/${workspaceId}/connections/create`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
