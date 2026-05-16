const rawCounterId = import.meta.env.VITE_YANDEX_METRIKA_COUNTER_ID || ''
export const METRIKA_COUNTER_ID = Number(rawCounterId)

const isBrowser = typeof window !== 'undefined' && typeof document !== 'undefined'

function canUseMetrika() {
  return isBrowser && Number.isFinite(METRIKA_COUNTER_ID) && METRIKA_COUNTER_ID > 0
}

export function initMetrika() {
  if (!canUseMetrika()) return
  window.dataLayer = window.dataLayer || []
  if (window.__postbridgeMetrikaInitialized) return
  window.__postbridgeMetrikaInitialized = true

  window.ym = window.ym || function ymStub() {
    ;(window.ym.a = window.ym.a || []).push(arguments)
  }
  window.ym.l = Date.now()

  const firstScript = document.getElementsByTagName('script')[0]
  const metrikaScript = document.createElement('script')
  metrikaScript.async = true
  metrikaScript.src = 'https://mc.yandex.ru/metrika/tag.js'
  firstScript.parentNode.insertBefore(metrikaScript, firstScript)

  window.ym(METRIKA_COUNTER_ID, 'init', {
    clickmap: true,
    trackLinks: true,
    accurateTrackBounce: true,
    webvisor: true,
    defer: true,
    ecommerce: 'dataLayer',
  })
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
