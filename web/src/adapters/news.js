import { api } from './apiClient'

export function listNews({ limit = 10, offset = 0, query = '' } = {}) {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })
  if (query) params.set('q', query)
  return api(`/api/news?${params}`)
}

export function getNewsItem(slug) {
  const source = new URLSearchParams(typeof window === 'undefined' ? '' : window.location.search).get('utm_source')
  const query = source ? `?${new URLSearchParams({ utm_source: source })}` : ''
  return api(`/api/news/${encodeURIComponent(slug || '')}${query}`, { headers: { 'X-Postbridge-News-View': '1' } })
}
