export const CORE_APP_BASE = '/api/app'

export function isSelfhostMode() {
  if (import.meta.env.VITE_POSTBRIDGE_APP_MODE === 'selfhost') return true
  if (typeof window === 'undefined') return false
  return window.location.pathname === '/web' || window.location.pathname.startsWith('/web/')
}

export async function fetchAppJson(path, options = {}) {
  const response = await fetch(`${CORE_APP_BASE}${path}`, options)
  return response.ok ? response.json() : null
}

export function fetchRuntimeConfig() {
  return fetchAppJson('/runtime-config')
}
