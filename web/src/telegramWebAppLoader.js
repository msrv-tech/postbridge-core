/**
 * A synchronous <script src="telegram.org/..."> in index.html blocks the first
 * paint site-wide while the Telegram CDN responds. This loader keeps it lazy
 * and only activates for likely Mini App / embedded Telegram browsers.
 */

const SCRIPT_SRC = 'https://telegram.org/js/telegram-web-app.js'

let loadPromise = null

export function isLikelyTelegramEmbedded() {
  if (typeof window === 'undefined') return false
  try {
    if (window.TelegramWebviewProxy) return true
    if (window.Telegram?.WebApp) return true
    if (/Telegram/i.test(navigator.userAgent || '')) return true
    const href = window.location.href
    if (/tgWebApp/i.test(href)) return true
  } catch {
    /* ignore */
  }
  return false
}

export function ensureTelegramWebAppScript() {
  if (window.Telegram?.WebApp) return Promise.resolve()
  if (loadPromise) return loadPromise
  loadPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-postbridge-tg-sdk="1"]`)
    if (existing) {
      existing.addEventListener('load', () => resolve())
      existing.addEventListener('error', () => reject(new Error('telegram sdk load error')))
      return
    }
    const s = document.createElement('script')
    s.src = SCRIPT_SRC
    s.async = true
    s.dataset.postbridgeTgSdk = '1'
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('telegram sdk load error'))
    document.head.appendChild(s)
  })
  return loadPromise
}
