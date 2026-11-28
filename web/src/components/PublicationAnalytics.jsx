import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useI18n } from '../i18n'
import PlatformLogo from './PlatformLogo'
import { publicationAnalytics } from '../adapters/analytics'
import '../pages/Analytics.css'

export const metricNames = ['views', 'impressions', 'reach', 'likes', 'reactions', 'comments', 'reposts', 'shares', 'quotes', 'bookmarks', 'saves']
const allMetrics = [...metricNames, 'followers', 'subscribers', 'members']

function formatDate(value, locale) {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function Timeline({ item, locale, t }) {
  const history = item.history || []
  const keys = allMetrics.filter((key) => history.some((entry) => entry.metrics[key] != null))
  const [requestedMetric, setRequestedMetric] = useState('')
  const key = keys.includes(requestedMetric) ? requestedMetric : keys[0]
  if (!key || history.length < 2) return null
  const points = [...history].reverse().filter((entry) => entry.metrics[key] != null)
  const maximum = Math.max(1, ...points.map((entry) => entry.metrics[key]))
  return <details>
    <summary>{t('analytics.history')}</summary>
    <label>{t('analytics.metric')}
      <select value={key} onChange={(event) => setRequestedMetric(event.target.value)}>
        {keys.map((name) => <option key={name} value={name}>{t(`analytics.metric.${name}`)}</option>)}
      </select>
    </label>
    <ul className="analytics-timeline">
      {points.map((entry) => <li key={entry.collected_at}>
        <time dateTime={entry.collected_at}>{formatDate(entry.collected_at, locale)}</time>
        <meter min={0} max={maximum} value={entry.metrics[key]} aria-label={t(`analytics.metric.${key}`)} />
        <span>{entry.metrics[key].toLocaleString(locale)}</span>
      </li>)}
    </ul>
  </details>
}

export function PublicationAnalyticsRows({ items, workspaceId, showPost = true }) {
  const { t, locale } = useI18n()
  return <div className="analytics-rows">
    {items.map((item) => <article className="analytics-receipt" key={item.id}>
      <div className="analytics-receipt-heading">
        <strong><PlatformLogo platform={item.platform} /> {item.channel_title || item.platform}</strong>
        {item.url && /^https:\/\//i.test(item.url) && <a href={item.url} target="_blank" rel="noopener noreferrer">{t('analytics.original')}</a>}
      </div>
      {showPost && <Link to={`/workspaces/${encodeURIComponent(workspaceId)}/content/${encodeURIComponent(item.content_id)}`}>
        {item.title || t('analytics.untitled')}
      </Link>}
      <p className="muted"><time dateTime={item.published_at}>{formatDate(item.published_at, locale)}</time></p>
      <dl className="analytics-metrics">
        {metricNames.filter((name) => item.latest?.metrics[name] != null).map((name) => <div key={name}>
          <dt>{t(`analytics.metric.${name}`)}</dt><dd>{item.latest.metrics[name].toLocaleString(locale)}</dd>
        </div>)}
      </dl>
      {item.latest && <p className="muted">{t('analytics.collected', { date: formatDate(item.latest.collected_at, locale) })}</p>}
      {item.status !== 'available' && <p className="muted">{t(`analytics.status.${item.detail || item.status}`)}</p>}
      <Timeline item={item} locale={locale} t={t} />
    </article>)}
  </div>
}

export function ChannelAudienceRows({ audience }) {
  const { locale, t } = useI18n()
  if (!audience?.length) return null
  return <section className="card">
    <h2>{t('analytics.audience')}</h2>
    <div className="analytics-rows">
      {audience.map((item) => <article key={item.id} className="analytics-receipt">
        <div className="analytics-receipt-heading">
          <strong><PlatformLogo platform={item.platform} /> {item.channel_title || item.platform}</strong>
        </div>
        {item.latest && <dl className="analytics-metrics">
          {Object.entries(item.latest.metrics).map(([name, value]) => <div key={name}>
            <dt>{t(`analytics.metric.${name}`)}</dt><dd>{value.toLocaleString(locale)}</dd>
          </div>)}
        </dl>}
        {item.status === 'available'
          ? item.latest && <p className="muted">{t('analytics.collected', { date: formatDate(item.latest.collected_at, locale) })}</p>
          : <p className="muted">{t(`analytics.status.${item.detail || item.status}`)}</p>}
        <Timeline item={item} locale={locale} t={t} />
      </article>)}
    </div>
  </section>
}

export default function PostPublicationAnalytics({ workspaceId, contentId }) {
  const { t } = useI18n()
  const [result, setResult] = useState(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let current = true
    publicationAnalytics(workspaceId, { content_id: contentId, limit: 100 })
      .then((data) => { if (current) setResult(data) })
      .catch(() => { if (current) setError(true) })
    return () => { current = false }
  }, [workspaceId, contentId])
  return <section className="card publication-analytics">
    <h2>{t('analytics.title')}</h2>
    {error ? <p role="alert">{t('analytics.loadError')}</p> : result
      ? (result.items.length ? <PublicationAnalyticsRows items={result.items} workspaceId={workspaceId} showPost={false} />
        : <p>{t('analytics.empty')}</p>)
      : <p role="status">{t('analytics.loading')}</p>}
  </section>
}
