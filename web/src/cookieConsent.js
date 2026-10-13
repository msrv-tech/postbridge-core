export const COOKIE_CONSENT_KEY = 'postbridge.analytics-consent'
export const COOKIE_CONSENT_VERSION = 1

export function readCookieConsent() {
  try {
    const record = JSON.parse(window.localStorage.getItem(COOKIE_CONSENT_KEY))
    if (record?.version !== COOKIE_CONSENT_VERSION) return null
    return ['accepted', 'rejected'].includes(record.value) ? record.value : null
  } catch {
    return null
  }
}

export function writeCookieConsent(value) {
  if (!['accepted', 'rejected'].includes(value)) return false
  try {
    window.localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify({
      version: COOKIE_CONSENT_VERSION, value, decidedAt: new Date().toISOString(),
    }))
    return true
  } catch {
    return false
  }
}
