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
  return api(`/api/news/${encodeURIComponent(slug || '')}`)
}
