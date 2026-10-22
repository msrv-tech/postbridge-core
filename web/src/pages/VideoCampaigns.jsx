import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import { useAuth } from '../useAuth'
import { useI18n } from '../i18n'
import { clearToken } from '../adapters/sessionToken'
import { api } from '../adapters/apiClient'
import { getVideo } from '../adapters/video'
import './VideoStudio.css'

export default function VideoCampaigns() {
  const { workspaceId } = useParams()
  const [query] = useSearchParams()
  const { user } = useAuth()
  const { t } = useI18n()
  const navigate = useNavigate()
  const [items, setItems] = useState([]), [selected, setSelected] = useState(''), [report, setReport] = useState(null)
  const [source, setSource] = useState(null), [name, setName] = useState(''), [landing, setLanding] = useState('')
  const [internal, setInternal] = useState(false), [label, setLabel] = useState(''), [promo, setPromo] = useState(''), [minutes, setMinutes] = useState('')
  const [key, setKey] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const endpoint = `/workspaces/${workspaceId}/video-campaigns`
  const number = (value) => value == null ? t('videoCampaign.noData') : value.toLocaleString()
  const load = async () => { const result = await api(endpoint); setItems(result.items); return result.items }
  const refresh = async () => { if (selected) setReport(await api(`${endpoint}/${selected}`)) }
  useEffect(() => {
    let active = true
    setItems([]); setSelected(''); setReport(null); setSource(null); setKey(null); setError('')
    Promise.all([api(endpoint), query.get('project') ? getVideo(workspaceId, query.get('project')) : null])
      .then(([list, project]) => { if (active) { setItems(list.items); setSelected(list.items[0]?.id || ''); setSource(project) } })
      .catch((e) => { if (active) setError(e.message) })
    return () => { active = false }
  }, [workspaceId, query.get('project')])
  useEffect(() => {
    let active = true
    setReport(null); setKey(null)
    if (selected) api(`${endpoint}/${selected}`).then((value) => { if (active) setReport(value) }).catch((e) => { if (active) setError(e.message) })
    return () => { active = false }
  }, [workspaceId, selected])
  const perform = async (action) => { if (busy) return; setBusy(true); setError(''); try { await action() } catch (e) { setError(e.message) } finally { setBusy(false) } }
  const create = (event) => { event.preventDefault(); perform(async () => {
    const value = await api(endpoint, { method: 'POST', body: JSON.stringify({ name, landing_url: landing, internal_attribution: internal }) })
    await load(); setSelected(value.id); setName(''); setLanding('')
  }) }
  const version = Number(query.get('version')) || source?.current?.version
  const revision = source?.revisions.find((value) => value.version === version)
  const add = (event) => { event.preventDefault(); perform(async () => {
    await api(`${endpoint}/${selected}/variants`, { method: 'POST', body: JSON.stringify({ project_id: source.id, version,
      label: label || `${source.brief.slice(0, 100)} · v${version}`, promo_code: promo || null, manual_minutes: minutes === '' ? null : Number(minutes) }) })
    await refresh()
  }) }
  const kinds = ['click', 'visit', 'registration', 'activation', 'reuse', 'payment']
  return <AppShell title={t('videoCampaign.title')} user={user} onLogout={() => { clearToken(); navigate('/') }}>
    <div className="video-studio video-campaigns">
      <Link to={`/workspaces/${workspaceId}/content/videos`}>{t('videoSchedule.openStudio')}</Link>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="muted">{t('videoCampaign.hint')}</p>
      <details className="card" open={!items.length}><summary>{t('videoCampaign.new')}</summary>
        <form onSubmit={create} className="video-create">
          <label>{t('videoCampaign.name')}<input required maxLength={160} value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>{t('videoCampaign.landing')}<input type="url" required value={landing} onChange={(e) => setLanding(e.target.value)} /></label>
          {user?.is_platform_admin && <label><input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} />{t('videoCampaign.internal')}</label>}
          <button className="btn" disabled={busy}>{t('videoCampaign.create')}</button>
        </form>
      </details>
      {!!items.length && <label>{t('videoCampaign.select')}<select value={selected} onChange={(e) => setSelected(e.target.value)}>{items.map((item) => <option value={item.id} key={item.id}>{item.name}{item.status === 'archived' ? ` · ${t('videoCampaign.archived')}` : ''}</option>)}</select></label>}
      {report && <>
        <section className="card">
          <h2>{report.name}</h2><label>{t('videoCampaign.campaignLink')}<input readOnly value={report.tracking_url} onFocus={(e) => e.target.select()} /></label>
          <p>{t('videoCampaign.cost', { units: number(report.production.units), minutes: number(report.production.manual_minutes) })}{report.production.uncertain && ` · ${t('video.reconciliationHint')}`}</p>
          <div className="video-funnel">{kinds.map((kind) => <div key={kind}><strong>{number(report.events[kind])}</strong><span>{t(`videoCampaign.${kind}`)}</span></div>)}</div>
          {!!report.partial_events.length && <p className="muted">{t('videoCampaign.partial')}</p>}
          <p className="muted">{t(`videoCampaign.${report.recommendation}`)}</p>
          <button className="btn btn-secondary" disabled={busy || report.status !== 'active'} onClick={() => perform(async () => { await api(`${endpoint}/${selected}/refresh`, { method: 'POST' }); await refresh() })}>{t('videoCampaign.refresh')}</button>
          <p className="muted">{t('videoCampaign.metricsHint')}</p>
        </section>
        {source && revision?.status === 'succeeded' && report.status === 'active' && <details className="card" open><summary>{t('videoCampaign.addCurrent')}</summary>
          <form onSubmit={add}>
            <p>{source.brief} · v{version}</p>
            <label>{t('videoCampaign.label')}<input maxLength={160} value={label} onChange={(e) => setLabel(e.target.value)} /></label>
            <details><summary>{t('video.options')}</summary>
              <label>{t('videoCampaign.promo')}<input maxLength={64} pattern="[A-Za-z0-9_-]+" value={promo} onChange={(e) => setPromo(e.target.value)} /></label>
              <label>{t('videoCampaign.minutes')}<input type="number" min="0" max="1440" value={minutes} onChange={(e) => setMinutes(e.target.value)} /></label>
            </details><button className="btn" disabled={busy}>{t('videoCampaign.add')}</button>
          </form>
        </details>}
        {report.variants.map((variant) => <section className="card" key={variant.id}>
          <h2><Link to={`/workspaces/${workspaceId}/content/videos/${variant.project_id}?version=${variant.version}`}>{variant.label}</Link> · v{variant.version} · {variant.language?.toUpperCase()}</h2>
          <label>{t('videoCampaign.variantLink')}<input readOnly value={variant.tracking_url} onFocus={(e) => e.target.select()} /></label>
          {variant.promo_code && <p>{t('videoCampaign.promo')}: {variant.promo_code}</p>}
          <div className="video-funnel">{kinds.map((kind) => <div key={kind}><strong>{number(variant.events[kind])}</strong><span>{t(`videoCampaign.${kind}`)}</span></div>)}</div>
          {variant.publication?.targets.map((target) => {
            const metric = variant.metrics.find((value) => value.target_id === target.id)
            return <p key={target.id}>{target.platform} · {target.status}{' · '}{t('videoCampaign.views')}: {number(metric?.metrics?.views?.value)}{' · '}{t('videoCampaign.likes')}: {number(metric?.metrics?.likes?.value)}{' · '}{t('videoCampaign.comments')}: {number(metric?.metrics?.comments?.value)}{target.external_url && <> · <a href={target.external_url} target="_blank" rel="noreferrer">{t('videoPost.open')}</a></>}</p>
          })}
          {!variant.publication && <p className="muted">{t('videoCampaign.notPublished')}</p>}
        </section>)}
        <details className="card"><summary>{t('videoCampaign.integration')}</summary>
          <p>{t(report.internal_attribution ? 'videoCampaign.internalHint' : 'videoCampaign.integrationHint')}</p>
          {!report.internal_attribution && report.status === 'active' && <button className="btn btn-secondary" disabled={busy} onClick={() => perform(async () => { setKey(await api(`${endpoint}/${selected}/event-key`, { method: 'POST' })); await load() })}>{t(report.external_events_enabled ? 'videoCampaign.rotateKey' : 'videoCampaign.createKey')}</button>}
          {key && <div role="status"><p>{t('videoCampaign.keyOnce')}</p><label>{t('videoCampaign.secret')}<input readOnly value={key.secret} /></label><code>{key.endpoint}</code></div>}
          <p className="muted">{t('videoCampaign.retention')}</p>
          {report.status === 'active' && <button className="btn btn-secondary" disabled={busy} onClick={() => perform(async () => { await api(`${endpoint}/${selected}/archive`, { method: 'POST' }); await load(); await refresh() })}>{t('videoSchedule.archive')}</button>}
        </details>
      </>}
    </div>
  </AppShell>
}
