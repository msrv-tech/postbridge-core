import { api } from './adapters/apiClient'
import { isSelfhostMode } from './adapters/runtime'
const storageKey = 'postbridge_video_ref'
// A signed first-touch reference contains no user identity. Never infer conversions here.
export function captureVideoAttribution() {
  if (isSelfhostMode()) return
  try {
    const url = new URL(window.location.href)
    const ticket = url.searchParams.get('pb_ref')
    if (!ticket) return
    if (ticket.startsWith('v1.') && ticket.length <= 2048 && !sessionStorage.getItem(storageKey)) sessionStorage.setItem(storageKey, ticket)
    url.searchParams.delete('pb_ref')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  } catch { /* Storage may be disabled. */ }
}
let pending
export async function claimVideoAttribution() {
  if (isSelfhostMode() || pending) return pending
  let ticket
  try { ticket = sessionStorage.getItem(storageKey) } catch { return }
  if (!ticket) return
  pending = api('/video-attribution/claim', { method: 'POST', body: JSON.stringify({ ticket }) })
    .then(() => sessionStorage.removeItem(storageKey))
    .catch((error) => { if (error.status === 400) sessionStorage.removeItem(storageKey) })
    .finally(() => { pending = null })
  return pending
}
