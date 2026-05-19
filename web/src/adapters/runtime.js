export const CORE_APP_BASE = '/api/app'

export function isSelfhostMode() {
  if (import.meta.env.VITE_POSTBRIDGE_APP_MODE === 'selfhost') return true
  if (typeof window === 'undefined') return false
  return window.location.pathname === '/web' || window.location.pathname.startsWith('/web/')
}

export async function fetchAppJson(path, options = {}) {
  const response = await fetch(`${CORE_APP_BASE}${path}`, options)
  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }
  if (response.ok) return data
  if (response.status === 404) return null

  const validation = Array.isArray(data?.details?.errors) ? data.details.errors[0] : null
  const loc = Array.isArray(validation?.loc) ? validation.loc.filter(Boolean).join('.') : ''
  const validationMessage = validation?.msg ? (loc ? `${loc}: ${validation.msg}` : validation.msg) : ''
  const nonJsonMessage = text ? text.slice(0, 300) : ''
  const message = validationMessage || data?.message || data?.detail || nonJsonMessage || `HTTP ${response.status}`
  const error = new Error(message)
  error.status = response.status
  error.code = data?.code
  error.details = data?.details || {}
  throw error
}

export function fetchRuntimeConfig() {
  return fetchAppJson('/runtime-config')
}
