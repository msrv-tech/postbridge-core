import { readCookieConsent } from './cookieConsent'

const rawCounterId = import.meta.env.VITE_YANDEX_METRIKA_COUNTER_ID || ''
export const METRIKA_COUNTER_ID = Number(rawCounterId)

const isBrowser = typeof window !== 'undefined' && typeof document !== 'undefined'
let withdrawn = false

function canUseMetrika() {
  return isBrowser && Number.isFinite(METRIKA_COUNTER_ID) && METRIKA_COUNTER_ID > 0
    && !withdrawn && readCookieConsent() === 'accepted'
}

export function initMetrika() {
  if (!canUseMetrika()) return
  window[`disableYaCounter${METRIKA_COUNTER_ID}`] = false
  window.dataLayer = window.dataLayer || []
  if (window.__postbridgeMetrikaInitialized) return
  window.__postbridgeMetrikaInitialized = true

  window.ym = window.ym || function ymStub() {
    ;(window.ym.a = window.ym.a || []).push(arguments)
  }
  window.ym.l = Date.now()

  const metrikaScript = document.createElement('script')
  metrikaScript.async = true
  metrikaScript.src = 'https://mc.yandex.ru/metrika/tag.js'
  metrikaScript.id = 'postbridge-metrika'
  document.head.appendChild(metrikaScript)

  window.ym(METRIKA_COUNTER_ID, 'init', {
    clickmap: true,
    trackLinks: true,
    accurateTrackBounce: true,
    webvisor: true,
    defer: true,
    ecommerce: 'dataLayer',
  })
}

export function enableMetrika() {
  if (readCookieConsent() !== 'accepted') return
  withdrawn = false
  hitMetrika(window.location.pathname)
}

export function disableMetrika({ reload = true } = {}) {
  if (!isBrowser) return
  withdrawn = true
  window[`disableYaCounter${METRIKA_COUNTER_ID}`] = true
  const initialized = window.__postbridgeMetrikaInitialized
  if (initialized && typeof window.ym === 'function') {
    window.ym(METRIKA_COUNTER_ID, 'destruct')
    if (Array.isArray(window.ym.a)) window.ym.a.length = 0
  }
  document.getElementById('postbridge-metrika')?.remove()
  window.__postbridgeMetrikaInitialized = false
  // Reload after withdrawal to stop an in-flight script and its event handlers.
  if (initialized && reload) window.location.reload()
}

export function hitMetrika(url) {
  if (!canUseMetrika()) return
  initMetrika()
  window.ym(METRIKA_COUNTER_ID, 'hit', url)
}

export function reachMetrikaGoal(goal, params = {}) {
  if (!canUseMetrika() || !goal) return
  initMetrika()
  window.ym(METRIKA_COUNTER_ID, 'reachGoal', goal, params)
}
