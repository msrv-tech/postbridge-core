const AUTH_RETURN_TO_KEY = 'postbridge.auth_return_to'
const OAUTH_CALLBACK_EXPECTED_KEY = 'postbridge.oauth_callback_expected'
const OAUTH_CALLBACK_TTL_MS = 10 * 60 * 1000

export function normalizeAuthReturnTo(value) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) {
    return null
  }
  try {
    const parsed = new URL(value, window.location.origin)
    if (parsed.origin !== window.location.origin || parsed.pathname !== '/oauth/authorize') {
      return null
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch {
    return null
  }
}

export function rememberAuthReturnTo(value) {
  const normalized = normalizeAuthReturnTo(value)
  if (normalized) {
    window.sessionStorage.setItem(AUTH_RETURN_TO_KEY, normalized)
  }
  return normalized
}

export function consumeAuthReturnTo() {
  const stored = window.sessionStorage.getItem(AUTH_RETURN_TO_KEY)
  window.sessionStorage.removeItem(AUTH_RETURN_TO_KEY)
  return normalizeAuthReturnTo(stored)
}

export function markOAuthCallbackExpected() {
  window.sessionStorage.setItem(OAUTH_CALLBACK_EXPECTED_KEY, String(Date.now()))
}

export function consumeOAuthCallbackExpected() {
  const stored = window.sessionStorage.getItem(OAUTH_CALLBACK_EXPECTED_KEY)
  window.sessionStorage.removeItem(OAUTH_CALLBACK_EXPECTED_KEY)
  if (!stored) return false
  const startedAt = Number(stored)
  if (!Number.isFinite(startedAt)) return false
  return Date.now() - startedAt <= OAUTH_CALLBACK_TTL_MS
}
