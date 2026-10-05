function marketFromHostname(hostname) {
  const host = String(hostname || '').trim().toLowerCase()
  if (host === 'postbridge.ru' || host.endsWith('.postbridge.ru')) return 'ru'
  if (host === 'postbridge.io' || host.endsWith('.postbridge.io')) return 'io'
  return null
}

function marketFromConfiguredBaseUrl() {
  const configuredBaseUrl = String(import.meta.env.VITE_POSTBRIDGE_PUBLIC_BASE_URL || '').trim()
  if (!configuredBaseUrl) return null
  try {
    return marketFromHostname(new URL(configuredBaseUrl).hostname)
  } catch {
    return null
  }
}

export function getPublicMarket() {
  if (typeof window !== 'undefined') {
    const runtimeMarket = marketFromHostname(window.location.hostname)
    if (runtimeMarket) return runtimeMarket
  }
  return marketFromConfiguredBaseUrl() || 'io'
}

export function getPublicBrandName() {
  return getPublicMarket() === 'ru' ? 'Postbridge.ru' : 'Postbridge.io'
}
