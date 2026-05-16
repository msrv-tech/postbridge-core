import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { clearToken } from '../adapters/sessionToken'
import { listChannelRegistry } from '../adapters/channels'
import {
  cleanupAgentRuntime,
  compactAgentEmbeddings,
  getAgentAnalyticsOverview,
  getAgentEmbeddingsLifecycle,
  getAgentAnalyticsQuality,
  getAgentAnalyticsTimeseries,
  listAgentPolicies,
  maintainAgentEmbeddings,
  reindexAgentChannelEmbeddings,
  reindexAgentEmbeddingDrift,
  rotateAgentChannelEmbeddings,
  upsertAgentPolicy,
} from '../adapters/agent'
import { useAuth } from '../useAuth'
import AppShell from '../components/AppShell'
import { useI18n } from '../i18n'

function isAgentEditorialChannel(channel) {
  return channel?.platform === 'postbridge'
}

function formatPercent(value) {
  if (typeof value !== 'number') return '—'
  return `${Math.round(value * 100)}%`
}

function formatNumber(value) {
  return typeof value === 'number' ? String(value) : '—'
}

function autonomyModeLabel(value, t) {
  const labels = {
    full_manual: t('topicScout.autonomy.approval'),
    draft_approval: t('topicScout.autonomy.approval'),
    plan_approval: t('topicScout.autonomy.approval'),
    guarded_auto_publish: t('topicScout.autonomy.auto'),
  }
  return labels[value] || value || '—'
}

function actionLabel(value, t) {
  const labels = {
    reindex: t('agentOps.embedding.action.reindex'),
    rotate: t('agentOps.embedding.action.rotate'),
    drift: t('agentOps.embedding.action.drift'),
    maintenance: t('agentOps.embedding.action.maintenance'),
    compact: t('agentOps.embedding.action.compact'),
    cleanup: t('agentOps.embedding.action.cleanup'),
  }
  return labels[value] || value || t('agentOps.embedding.action.operation')
}

function PolicyEditor({
  title,
  channels,
  channelId,
  setChannelId,
  policyJson,
  setPolicyJson,
  saving,
  onSave,
}) {
  const { t } = useI18n()
  return (
    <div className="card" style={{ display: 'grid', gap: '0.75rem' }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {channels && (
        <div className="form-group">
          <label htmlFor={`${title}-channel`}>{t('agentOps.policy.channelOverride')}</label>
          <select
            id={`${title}-channel`}
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            className="form-control"
          >
            <option value="">{t('agentOps.policy.workspaceDefault')}</option>
            {channels.map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.title || channel.platform_channel_id}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="form-group">
        <label htmlFor={`${title}-policy`}>{t('agentOps.policy.json')}</label>
        <textarea
          id={`${title}-policy`}
          value={policyJson}
          onChange={(e) => setPolicyJson(e.target.value)}
          className="form-control"
          rows={12}
        />
      </div>
      <button type="button" className="btn" onClick={onSave} disabled={saving}>
        {saving ? t('common.savingShort') : t('agentOps.policy.save')}
      </button>
    </div>
  )
}

export default function AgentOps() {
  const { t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const [channels, setChannels] = useState([])
  const [overview, setOverview] = useState(null)
  const [timeseries, setTimeseries] = useState(null)
  const [quality, setQuality] = useState(null)
  const [policies, setPolicies] = useState([])
  const [embeddingsLifecycle, setEmbeddingsLifecycle] = useState(null)
  const [tenantPolicyJson, setTenantPolicyJson] = useState('{\n  "requires_review": true\n}')
  const [channelPolicyId, setChannelPolicyId] = useState('')
  const [channelPolicyJson, setChannelPolicyJson] = useState('{\n  "requires_review": true\n}')
  const [loading, setLoading] = useState(true)
  const [savingTenant, setSavingTenant] = useState(false)
  const [savingChannel, setSavingChannel] = useState(false)
  const [embeddingActionLoading, setEmbeddingActionLoading] = useState('')
  const [error, setError] = useState('')
  const [saveMessage, setSaveMessage] = useState('')

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  const loadData = () => {
    if (!workspaceId) return
    setLoading(true)
    setError('')
    Promise.all([
      listChannelRegistry(workspaceId),
      getAgentAnalyticsOverview(workspaceId),
      getAgentAnalyticsTimeseries(workspaceId, { days: 30 }),
      getAgentAnalyticsQuality(workspaceId, { days: 30 }),
      listAgentPolicies(workspaceId),
      getAgentEmbeddingsLifecycle(workspaceId, { channelLimit: 20, channelOffset: 0 }),
    ])
      .then(([channelsResponse, overviewResponse, timeseriesResponse, qualityResponse, policiesResponse, lifecycleResponse]) => {
        const editorialChannels = (channelsResponse.items || []).filter(isAgentEditorialChannel)
        const policyItems = policiesResponse.items || []
        setChannels(editorialChannels)
        setOverview(overviewResponse)
        setTimeseries(timeseriesResponse)
        setQuality(qualityResponse)
        setPolicies(policyItems)
        setEmbeddingsLifecycle(lifecycleResponse)

        const tenantPolicy = policyItems.find((item) => !item.channel_id)
        if (tenantPolicy) {
          setTenantPolicyJson(JSON.stringify(tenantPolicy.policy || {}, null, 2))
        }
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (!user?.is_platform_admin) {
      setLoading(false)
      return
    }
    loadData()
  }, [workspaceId, user?.is_platform_admin])

  useEffect(() => {
    if (!channelPolicyId) {
      setChannelPolicyJson('{\n  "requires_review": true\n}')
      return
    }
    const policy = policies.find((item) => item.saas_channel_id === channelPolicyId)
    setChannelPolicyJson(JSON.stringify(policy?.policy || { requires_review: true }, null, 2))
  }, [channelPolicyId, policies])

  const sourceRows = useMemo(
    () => Array.isArray(quality?.sources) ? quality.sources.slice(0, 5) : [],
    [quality]
  )
  const recommendationRows = useMemo(
    () => Array.isArray(quality?.policy_recommendations) ? quality.policy_recommendations.slice(0, 5) : [],
    [quality]
  )
  const workflowRows = useMemo(
    () => Array.isArray(quality?.workflow_presets) ? quality.workflow_presets.slice(0, 5) : [],
    [quality]
  )
  const angleRows = useMemo(
    () => Array.isArray(quality?.angles) ? quality.angles.slice(0, 5) : [],
    [quality]
  )
  const timeseriesRows = useMemo(
    () => Array.isArray(timeseries?.items) ? timeseries.items.slice(-7) : [],
    [timeseries]
  )

  if (!user?.is_platform_admin) {
    return (
      <div className="container" style={{ paddingTop: '2rem' }}>
        <p className="error">{t('admin.forbidden')}</p>
        <Link to="/">{t('common.home')}</Link>
      </div>
    )
  }

  const saveTenantPolicy = async () => {
    if (!workspaceId) return
    setSavingTenant(true)
    setError('')
    setSaveMessage('')
    try {
      await upsertAgentPolicy(workspaceId, {
        policy: JSON.parse(tenantPolicyJson),
      })
      setSaveMessage(t('agentOps.policy.workspaceSaved'))
      loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingTenant(false)
    }
  }

  const saveChannelPolicy = async () => {
    if (!workspaceId) return
    if (!channelPolicyId) {
      setError(t('agentOps.policy.selectChannel'))
      return
    }
    setSavingChannel(true)
    setError('')
    setSaveMessage('')
    try {
      await upsertAgentPolicy(workspaceId, {
        channel_id: channelPolicyId,
        policy: JSON.parse(channelPolicyJson),
      })
      setSaveMessage(t('agentOps.policy.channelSaved'))
      loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingChannel(false)
    }
  }

  const runEmbeddingAction = async (action, handler) => {
    setEmbeddingActionLoading(action)
    setError('')
    setSaveMessage('')
    try {
      const result = await handler()
      setSaveMessage(`${actionLabel(action, t)}: ${result.status || 'completed'}`)
      loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setEmbeddingActionLoading('')
    }
  }

  return (
    <AppShell
      title={t('agentOps.title')}
      subtitle={t('agentOps.subtitle')}
      user={user}
      onLogout={handleLogout}
      showAdminLink
      actions={
        <>
          <Link to={`/workspaces/${workspaceId}/agents/topic-scout`} className="btn btn-secondary btn-small">
            {t('agents.nav.topicScout')}
          </Link>
          <Link to={`/workspaces/${workspaceId}/agents/candidates`} className="btn btn-secondary btn-small">
            {t('agents.nav.candidates')}
          </Link>
        </>
      }
    >
      {error && <p className="error">{error}</p>}
      {saveMessage && <p className="muted">{saveMessage}</p>}
      {loading && <div className="card"><p className="muted">{t('common.loading')}</p></div>}

      {!loading && (
        <div style={{ display: 'grid', gap: '1rem' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
            <div className="card">
              <p className="muted" style={{ marginTop: 0 }}>{t('agentOps.metric.runs')}</p>
              <h3 style={{ marginBottom: 0 }}>{formatNumber(overview?.total_runs || overview?.run_count)}</h3>
            </div>
            <div className="card">
              <p className="muted" style={{ marginTop: 0 }}>{t('agentOps.metric.pendingReviews')}</p>
              <h3 style={{ marginBottom: 0 }}>{formatNumber(overview?.pending_reviews || overview?.review_pending_count)}</h3>
            </div>
            <div className="card">
              <p className="muted" style={{ marginTop: 0 }}>{t('agents.nav.candidates')}</p>
              <h3 style={{ marginBottom: 0 }}>{formatNumber(overview?.candidate_count)}</h3>
            </div>
            <div className="card">
              <p className="muted" style={{ marginTop: 0 }}>{t('agentOps.metric.conversion')}</p>
              <h3 style={{ marginBottom: 0 }}>{formatPercent(overview?.conversion_rate)}</h3>
            </div>
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>{t('agentOps.timeseries.title')}</h3>
            {timeseriesRows.length === 0 && <p className="muted">{t('agentOps.noData')}</p>}
            {timeseriesRows.length > 0 && (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {timeseriesRows.map((row, index) => (
                  <li key={`${row.day || row.date || index}`} style={{ padding: '0.6rem 0', borderBottom: '1px solid var(--border)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
                      <strong>{row.day || row.date || `day-${index}`}</strong>
                      <span className="muted">
                        {t('agentOps.timeseries.item', { runs: formatNumber(row.run_count), reviews: formatNumber(row.review_count || row.review_resolved_count) })}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1rem' }}>
            <div className="card">
              <h3 style={{ marginTop: 0 }}>{t('agentOps.sources.title')}</h3>
              {sourceRows.length === 0 && <p className="muted">{t('agentOps.noData')}</p>}
              {sourceRows.length > 0 && (
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {sourceRows.map((row, index) => (
                    <li key={`${row.domain || index}`} style={{ padding: '0.6rem 0', borderBottom: '1px solid var(--border)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
                        <strong>{row.domain || t('agentOps.unknown')}</strong>
                        <span className="muted">
                          {t('agentOps.sources.meta', { approval: formatPercent(row.approval_rate), trust: row.trust_label || '—' })}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="card">
              <h3 style={{ marginTop: 0 }}>{t('agentOps.recommendations.title')}</h3>
              {recommendationRows.length === 0 && <p className="muted">{t('agentOps.noData')}</p>}
              {recommendationRows.length > 0 && (
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {recommendationRows.map((row, index) => (
                    <li key={`${row.channel_id || row.current_policy || index}`} style={{ padding: '0.6rem 0', borderBottom: '1px solid var(--border)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
                        <strong>{autonomyModeLabel(row.recommended_policy, t)}</strong>
                        <span className="muted">{row.confidence || '—'}</span>
                      </div>
                      <p className="muted" style={{ margin: '0.3rem 0 0 0' }}>
                        {row.reason || row.recommendation || '—'}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="card">
              <h3 style={{ marginTop: 0 }}>{t('agentOps.workflow.title')}</h3>
              {workflowRows.length === 0 && <p className="muted">{t('agentOps.noData')}</p>}
              {workflowRows.length > 0 && (
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {workflowRows.map((row, index) => (
                    <li key={`${row.workflow_preset || index}`} style={{ padding: '0.6rem 0', borderBottom: '1px solid var(--border)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
                        <strong>{row.workflow_preset || '—'}</strong>
                        <span className="muted">{t('agentOps.conversionMeta', { value: formatPercent(row.conversion_rate) })}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="card">
              <h3 style={{ marginTop: 0 }}>{t('agentOps.angles.title')}</h3>
              {angleRows.length === 0 && <p className="muted">{t('agentOps.noData')}</p>}
              {angleRows.length > 0 && (
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {angleRows.map((row, index) => (
                    <li key={`${row.angle_family || row.angle || index}`} style={{ padding: '0.6rem 0', borderBottom: '1px solid var(--border)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
                        <strong>{row.angle_family || row.angle || '—'}</strong>
                        <span className="muted">{t('agentOps.conversionMeta', { value: formatPercent(row.conversion_rate) })}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1rem' }}>
            <PolicyEditor
              title={t('agentOps.policy.workspaceTitle')}
              channels={null}
              channelId=""
              setChannelId={() => {}}
              policyJson={tenantPolicyJson}
              setPolicyJson={setTenantPolicyJson}
              saving={savingTenant}
              onSave={saveTenantPolicy}
            />

            <PolicyEditor
              title={t('agentOps.policy.channelTitle')}
              channels={channels}
              channelId={channelPolicyId}
              setChannelId={setChannelPolicyId}
              policyJson={channelPolicyJson}
              setPolicyJson={setChannelPolicyJson}
              saving={savingChannel}
              onSave={saveChannelPolicy}
            />
          </div>

          <div className="card" style={{ display: 'grid', gap: '0.75rem' }}>
            <h3 style={{ marginTop: 0 }}>{t('agentOps.embedding.title')}</h3>
            <p className="muted" style={{ margin: 0 }}>
              {t('agentOps.embedding.text')}
            </p>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <span className="muted">{t('common.status')}: {embeddingsLifecycle?.status || '—'}</span>
              <span className="muted">{t('agentOps.embedding.backend')}: {embeddingsLifecycle?.vector_backend || '—'}</span>
              <span className="muted">{t('agentOps.embedding.nativeMode')}: {String(embeddingsLifecycle?.pgvector_native ?? '—')}</span>
            </div>
            {Array.isArray(embeddingsLifecycle?.channels) && embeddingsLifecycle.channels.length > 0 && (
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('agentOps.embedding.context')}</th>
                      <th>{t('agentOps.embedding.materials')}</th>
                      <th>{t('agentOps.embedding.stored')}</th>
                      <th>{t('agentOps.embedding.missing')}</th>
                      <th>{t('agentOps.embedding.stale')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {embeddingsLifecycle.channels.map((row, index) => (
                      <tr key={row.channel_id || index}>
                        <td>{row.channel_id || '—'}</td>
                        <td>{formatNumber(row.content_items_total)}</td>
                        <td>{formatNumber(row.stored_embeddings)}</td>
                        <td>{formatNumber(row.missing_embeddings)}</td>
                        <td>{formatNumber(row.stale_embeddings)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
              {t('agentOps.embedding.schedule')}
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '0.75rem' }}>
              <div style={{ display: 'grid', gap: '0.35rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={embeddingActionLoading === 'reindex' || !channelPolicyId}
                  onClick={() =>
                    runEmbeddingAction('reindex', () =>
                      reindexAgentChannelEmbeddings(workspaceId, channelPolicyId, { limit: 100, offset: 0 })
                    )
                  }
                >
                  {embeddingActionLoading === 'reindex' ? '...' : t('agentOps.embedding.button.reindex')}
                </button>
                <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
                  {t('agentOps.embedding.hint.reindex')}
                </p>
              </div>
              <div style={{ display: 'grid', gap: '0.35rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={embeddingActionLoading === 'rotate' || !channelPolicyId}
                  onClick={() =>
                    runEmbeddingAction('rotate', () =>
                      rotateAgentChannelEmbeddings(workspaceId, channelPolicyId, { limit: 100, offset: 0 })
                    )
                  }
                >
                  {embeddingActionLoading === 'rotate' ? '...' : t('agentOps.embedding.button.rotate')}
                </button>
                <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
                  {t('agentOps.embedding.hint.rotate')}
                </p>
              </div>
              <div style={{ display: 'grid', gap: '0.35rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={embeddingActionLoading === 'drift'}
                  onClick={() =>
                    runEmbeddingAction('drift', () =>
                      reindexAgentEmbeddingDrift(workspaceId, {
                        channel_id: channelPolicyId || null,
                        channel_limit: 20,
                        item_limit: 100,
                        channel_offset: 0,
                      })
                    )
                  }
                >
                  {embeddingActionLoading === 'drift' ? '...' : t('agentOps.embedding.button.drift')}
                </button>
                <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
                  {t('agentOps.embedding.hint.drift')}
                </p>
              </div>
              <div style={{ display: 'grid', gap: '0.35rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={embeddingActionLoading === 'maintenance'}
                  onClick={() =>
                    runEmbeddingAction('maintenance', () =>
                      maintainAgentEmbeddings(workspaceId, {
                        channel_id: channelPolicyId || null,
                        prune_orphans: true,
                        prune_malformed: true,
                        optimize_native: true,
                      })
                    )
                  }
                >
                  {embeddingActionLoading === 'maintenance' ? '...' : t('agentOps.embedding.button.maintenance')}
                </button>
                <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
                  {t('agentOps.embedding.hint.maintenance')}
                </p>
              </div>
              <div style={{ display: 'grid', gap: '0.35rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={embeddingActionLoading === 'compact'}
                  onClick={() =>
                    runEmbeddingAction('compact', () =>
                      compactAgentEmbeddings(workspaceId, {
                        channel_id: channelPolicyId || null,
                        optimize_native: true,
                      })
                    )
                  }
                >
                  {embeddingActionLoading === 'compact' ? '...' : t('agentOps.embedding.button.compact')}
                </button>
                <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
                  {t('agentOps.embedding.hint.compact')}
                </p>
              </div>
              <div style={{ display: 'grid', gap: '0.35rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={embeddingActionLoading === 'cleanup'}
                  onClick={() =>
                    runEmbeddingAction('cleanup', () =>
                      cleanupAgentRuntime(workspaceId, {})
                    )
                  }
                >
                  {embeddingActionLoading === 'cleanup' ? '...' : t('agentOps.embedding.button.cleanup')}
                </button>
                <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
                  {t('agentOps.embedding.hint.cleanup')}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  )
}
