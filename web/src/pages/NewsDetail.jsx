import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { getNewsItem } from '../adapters/news'
import { useI18n } from '../i18n'

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

function sameOpening(summary, body) {
  const cleanSummary = String(summary || '').replace(/\s+/g, ' ').trim()
  const cleanBody = String(body || '').replace(/\s+/g, ' ').trim()
  if (!cleanSummary || !cleanBody) return false
  return cleanBody.startsWith(cleanSummary.slice(0, 80))
}

export default function NewsDetail() {
  const { locale, t } = useI18n()
  const { slug } = useParams()
  const [item, setItem] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    getNewsItem(slug)
      .then((data) => {
        setItem(data?.item || null)
      })
      .catch((err) => {
        setItem(null)
        setError(err.message || t('newsDetail.error.notFound'))
      })
      .finally(() => setLoading(false))
  }, [slug, t])

  const body = item?.content_text || item?.summary || ''
  const showLead = item?.summary && !sameOpening(item.summary, body)

  return (
    <PublicLayout compact>
      <section className="section">
        <div className="container news-detail">
          <Link to="/news" className="eyebrow">
            {t('news.eyebrow')}
          </Link>

          {loading && <p className="muted">{t('newsDetail.loading')}</p>}

          {!loading && error && (
            <div className="empty-state">
              <h1 className="news-detail-title">{t('newsDetail.notFound')}</h1>
              <p className="muted">{error}</p>
              <Link to="/news" className="btn btn-secondary btn-small">
                {t('newsDetail.allNews')}
              </Link>
            </div>
          )}

          {!loading && !error && item && (
            <article>
              <h1 className="news-detail-title">{item.title}</h1>
              <p className="muted news-card-date">{formatDate(item.published_at, locale, t)}</p>
              {item.media_url && (
                <img className="news-detail-media" src={item.media_url} alt="" />
              )}
              {showLead && <p className="news-detail-lead">{item.summary}</p>}
              <div className="news-detail-body">{body}</div>
            </article>
          )}
        </div>
      </section>
    </PublicLayout>
  )
}
