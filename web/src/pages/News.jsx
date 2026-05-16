import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { listNews } from '../adapters/news'
import { PUBLIC_CHANNELS } from '../publicChannels'
import { useI18n } from '../i18n'

const PAGE_SIZE = 10

function previewText(value, maxLen = 260) {
  if (!value) return ''
  const plain = String(value).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
  return plain.length > maxLen ? plain.slice(0, maxLen - 1).trim() + '…' : plain
}

function newsKey(item, index) {
  return item.slug || item.link || `${item.title}-${index}`
}

function formatDate(value, locale, t) {
  if (!value) return t('common.noDate')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return t('common.noDate')
  return date.toLocaleDateString(locale === 'en' ? 'en-US' : 'ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

export default function News() {
  const { locale, t } = useI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [draftQuery, setDraftQuery] = useState(searchParams.get('q') || '')

  const query = searchParams.get('q') || ''
  const page = Math.max(1, Number(searchParams.get('page') || '1') || 1)
  const offset = (page - 1) * PAGE_SIZE
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  useEffect(() => {
    setDraftQuery(query)
  }, [query])

  useEffect(() => {
    setLoading(true)
    setError('')
    listNews({ limit: PAGE_SIZE, offset, query })
      .then((data) => {
        setItems(Array.isArray(data?.items) ? data.items : [])
        setTotal(Number(data?.total || 0))
      })
      .catch((err) => {
        setItems([])
        setTotal(0)
        setError(err.message || t('news.error.load'))
      })
      .finally(() => setLoading(false))
  }, [query, offset, t])

  const submitSearch = (event) => {
    event.preventDefault()
    const next = new URLSearchParams()
    const clean = draftQuery.trim()
    if (clean) next.set('q', clean)
    setSearchParams(next)
  }

  const goToPage = (nextPage) => {
    const next = new URLSearchParams(searchParams)
    if (nextPage <= 1) next.delete('page')
    else next.set('page', String(nextPage))
    setSearchParams(next)
  }

  return (
    <PublicLayout compact>
      <section className="section">
        <div className="container">
          <div className="section-heading">
            <div className="news-eyebrow-row">
              <span className="eyebrow">{t('news.eyebrow')}</span>
              {PUBLIC_CHANNELS.map((channel) => (
                <a
                  key={channel.id}
                  href={channel.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="eyebrow news-channel-link"
                >
                  {channel.label}
                </a>
              ))}
            </div>
            <h1 className="hero-title-small news-page-title">{t('news.title')}</h1>
          </div>

          <form
            onSubmit={submitSearch}
            className="card"
            style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginBottom: '1rem', flexWrap: 'wrap' }}
          >
            <input
              value={draftQuery}
              onChange={(event) => setDraftQuery(event.target.value)}
              placeholder={t('news.search.placeholder')}
              aria-label={t('news.search.aria')}
              style={{ flex: '1 1 260px' }}
            />
            <button type="submit" className="btn btn-small">
              {t('news.search.submit')}
            </button>
            {query && (
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => setSearchParams({})}
              >
                {t('common.reset')}
              </button>
            )}
          </form>

          {error && <p className="error">{error}</p>}
          {loading && <p className="muted">{t('news.loading')}</p>}
          {!loading && !error && total > 0 && (
            <p className="muted">
              {t('news.pagination.summary', { total, page: Math.min(page, totalPages), totalPages })}
            </p>
          )}

          {!loading && !error && items.length === 0 && (
            <div className="empty-state">
              <p className="muted">{query ? t('news.empty.search') : t('news.empty.default')}</p>
              <Link to="/" className="btn btn-secondary btn-small">
                {t('common.home')}
              </Link>
            </div>
          )}

          {!loading && !error && items.length > 0 && (
            <div className="news-list">
              {items.map((item, index) => {
                const detailUrl = item.slug ? `/news/${item.slug}` : item.link
                return (
                  <article
                    className={`card news-card${item.media_url ? ' has-media' : ''}`}
                    key={newsKey(item, index)}
                  >
                    {item.media_url && (
                      <img className="news-card-media" src={item.media_url} alt="" loading="lazy" />
                    )}
                    <div className="news-card-body">
                      <p className="muted news-card-date">{formatDate(item.published_at, locale, t)}</p>
                      <h3>{item.title}</h3>
                      <p className="news-card-preview">{previewText(item.summary)}</p>
                      {detailUrl && (
                        <p className="news-card-link">
                          <a href={detailUrl}>{t('news.readMore')}</a>
                        </p>
                      )}
                    </div>
                  </article>
                )
              })}
            </div>
          )}

          {!loading && totalPages > 1 && (
            <div className="hero-actions" style={{ justifyContent: 'center', marginTop: '1.5rem' }}>
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => goToPage(page - 1)}
                disabled={page <= 1}
              >
                {t('common.back')}
              </button>
              <span className="muted">
                {Math.min(page, totalPages)} / {totalPages}
              </span>
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => goToPage(page + 1)}
                disabled={page >= totalPages}
              >
                {t('common.next')}
              </button>
            </div>
          )}
        </div>
      </section>
    </PublicLayout>
  )
}
