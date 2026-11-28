import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { newsAnalytics, publicationAnalytics } from '../adapters/analytics'
import AppShell from '../components/AppShell'
import { ChannelAudienceRows, metricNames, PublicationAnalyticsRows } from '../components/PublicationAnalytics'
import { useI18n } from '../i18n'

const PAGE_SIZE = 20

function NewsTraffic({ workspaceId, days }) {
  const { t, locale } = useI18n()
  const [report, setReport] = useState(null)
  useEffect(() => {
    let current = true
    setReport(null)
    newsAnalytics(workspaceId, days).then((data) => { if (current) setReport(data) }).catch(() => {})
    return () => { current = false }
  }, [workspaceId, days])
  if (!report?.available) return null
  return <section className="card">
    <h2>{t('analytics.news')}</h2>
    <dl className="analytics-metrics">
      <div><dt>{t('analytics.articleRequests')}</dt><dd>{report.article_requests.toLocaleString(locale)}</dd></div>
      <div><dt>{t('analytics.feedRequests')}</dt><dd>{report.feed_requests.toLocaleString(locale)}</dd></div>
    </dl>
    {report.articles?.length > 0 && <ul className="analytics-news-list">
      {report.articles.map((item) => <li key={item.slug}>
        <a href={`/news/${encodeURIComponent(item.slug)}`}>{item.slug}</a>
        <span>{item.requests.toLocaleString(locale)}</span>
      </li>)}
    </ul>}
    {report.utm_sources?.length > 0 && <>
      <h3>{t('analytics.utmSources')}</h3>
      <ul className="analytics-news-list">
        {report.utm_sources.map((item) => <li key={item.source}>
          <span>{item.source}</span><span>{item.requests.toLocaleString(locale)}</span>
        </li>)}
      </ul>
    </>}
  </section>
}

export default function Analytics() {
  const { workspaceId } = useParams()
  return <WorkspaceAnalytics key={workspaceId} workspaceId={workspaceId} />
}

function WorkspaceAnalytics({ workspaceId }) {
  const { t } = useI18n()
  const [filters, setFilters] = useState({ days: '30', platform: '', channel_id: '', content_format: '', metric: '' })
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [channels, setChannels] = useState([])
  const [error, setError] = useState(false)

  const update = (key, value) => {
    setOffset(0)
    setFilters((current) => ({
      ...current, [key]: value, ...(key === 'platform' ? { channel_id: '', metric: '' } : {}),
    }))
  }
  useEffect(() => {
    let current = true
    setData(null)
    setError(false)
    publicationAnalytics(workspaceId, { ...filters, offset, limit: PAGE_SIZE }).then((result) => {
      if (current) {
        setData(result)
        setChannels(result.channels)
      }
    }).catch(() => { if (current) setError(true) })
    return () => { current = false }
  }, [workspaceId, filters, offset])

  const platforms = [...new Set(channels.map((channel) => channel.platform))].sort()
  const workspace = encodeURIComponent(workspaceId)
  return <AppShell title={t('analytics.title')} actions={
    <Link className="btn btn-secondary btn-small" to={`/workspaces/${workspace}/content/video-campaigns`}>{t('videoCampaign.title')}</Link>
  }>
    <div className="publication-analytics">
      <div className="card analytics-filters">
        <label>{t('analytics.period')}<select value={filters.days} onChange={(event) => update('days', event.target.value)}>
          {[7, 30, 90, 365].map((day) => <option value={day} key={day}>{t('analytics.days', { days: day })}</option>)}
        </select></label>
        <label>{t('analytics.platform')}<select value={filters.platform} onChange={(event) => update('platform', event.target.value)}>
          <option value="">{t('analytics.all')}</option>
          {platforms.map((platform) => <option key={platform} value={platform}>{platform}</option>)}
        </select></label>
        <label>{t('analytics.channel')}<select value={filters.channel_id} onChange={(event) => update('channel_id', event.target.value)}>
          <option value="">{t('analytics.all')}</option>
          {channels.filter((channel) => !filters.platform || channel.platform === filters.platform).map((channel) =>
            <option value={channel.id} key={channel.id}>{channel.title || channel.platform}</option>)}
        </select></label>
        <label>{t('analytics.format')}<select value={filters.content_format} onChange={(event) => update('content_format', event.target.value)}>
          <option value="">{t('analytics.all')}</option>
          <option value="post">{t('analytics.post')}</option>
          <option value="video">{t('analytics.video')}</option>
        </select></label>
        {filters.platform && <label>{t('analytics.order')}<select value={filters.metric} onChange={(event) => update('metric', event.target.value)}>
          <option value="">{t('analytics.recent')}</option>
          {metricNames.map((metric) => <option value={metric} key={metric}>{t(`analytics.metric.${metric}`)}</option>)}
        </select></label>}
      </div>
      {error ? <p role="alert">{t('analytics.loadError')}</p> : !data ? <p role="status">{t('analytics.loading')}</p>
        : data.items.length ? <PublicationAnalyticsRows items={data.items} workspaceId={workspaceId} /> : <p>{t('analytics.empty')}</p>}
      {(offset > 0 || data?.has_more) && <div className="inline-actions">
        <button className="btn btn-secondary" disabled={offset === 0} onClick={() => setOffset(offset - PAGE_SIZE)}>{t('analytics.previous')}</button>
        <button className="btn btn-secondary" disabled={!data?.has_more} onClick={() => setOffset(offset + PAGE_SIZE)}>{t('analytics.next')}</button>
      </div>}
      {offset === 0 && <ChannelAudienceRows audience={data?.audience} />}
      <NewsTraffic workspaceId={workspaceId} days={filters.days} />
    </div>
  </AppShell>
}
