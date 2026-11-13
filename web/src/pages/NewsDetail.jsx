import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { NewsBody, NewsMedia } from '../components/NewsContent'
import { newsMedia, newsText } from '../adapters/newsPresentation'
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

  const summary = newsText(item?.summary)
  const body = newsText(item?.content_text) || summary
  const media = newsMedia(item)
  const showLead = summary && !sameOpening(summary, body)

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
              <h1 className="news-detail-title">{newsText(item.title)}</h1>
              <p className="muted news-card-date">{formatDate(item.published_at, locale, t)}</p>
              <NewsMedia media={media} imageClassName="news-detail-media" />
              {showLead && <p className="news-detail-lead">{summary}</p>}
              <div className="news-detail-body"><NewsBody html={item.content_html} text={body} /></div>
            </article>
          )}
        </div>
      </section>
    </PublicLayout>
  )
}
