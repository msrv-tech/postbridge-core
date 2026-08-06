import { api } from './apiClient'

export function listAuthProviders() {
  return api('/auth/providers')
}

export function requestMagicLink(payload) {
  return api('/auth/magic-link/request', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function verifyMagicLink(payload) {
  return api('/auth/magic-link/verify', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function reviewLogin(payload) {
  return api('/auth/review-login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function authenticateMiniApp(endpoint, initData) {
  const url = endpoint === 'max' ? '/auth/max-mini-app' : '/auth/telegram-mini-app'
  return api(url, {
    method: 'POST',
    body: JSON.stringify({ init_data: initData }),
  })
}

export function startTelegramWebLinkSession() {
  return api('/auth/telegram-web/start', { method: 'POST' })
}

export function fetchTelegramWebLinkStatus(sessionToken) {
  const q = new URLSearchParams({ session_token: sessionToken })
  return api(`/auth/telegram-web/status?${q}`)
}
