import { fetchAppJson } from './runtime'
import { getToken } from './sessionToken'

export function getSelfhostSession(token = getToken()) {
  return fetchAppJson('/session', {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

export function bootstrapSelfhost(payload) {
  return fetchAppJson('/bootstrap', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function loginSelfhost(payload) {
  return fetchAppJson('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function startGitsellDeviceFlow(payload) {
  return fetchAppJson('/gitsell-device/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function pollGitsellDeviceFlow(payload) {
  return fetchAppJson('/gitsell-device/poll', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
