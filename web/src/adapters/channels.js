import { api } from './apiClient'

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
