const AUTH_RETURN_TO_KEY = 'postbridge.auth_return_to'

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
