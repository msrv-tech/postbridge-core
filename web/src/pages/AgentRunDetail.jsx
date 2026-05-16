import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { clearToken } from '../adapters/sessionToken'
import {
  getAgentRun,
  listAgentReviewQueue,
  listAgentRunSteps,
} from '../adapters/agent'
import { useAuth } from '../useAuth'
import AppShell from '../components/AppShell'
import AgentSectionLayout from '../components/AgentSectionLayout'
import { useI18n } from '../i18n'

function formatDate(value, locale) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(locale === 'en' ? 'en-US' : 'ru-RU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function agentModeLabel(value, t) {
  const labels = {
    post_copilot: t('agents.nav.editor'),
    topic_scout: t('agents.nav.topicScout'),
  }
  return labels[value] || value || t('agentRun.title')
}

function agentNavKey(value) {
  if (value === 'post_copilot') return 'editor'
  if (value === 'topic_scout') return 'topic-scout'
  return 'topic-scout'
}

function formatStepValue(value) {
  if (value == null || value === '') return null
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.filter(Boolean).join(', ')
  return null
}

function toolSummaryLabel(key, value, t) {
  if (value == null) return null
  if (typeof value === 'number' || typeof value === 'string') {
    return `${key}: ${String(value)}`
  }
  if (key === 'top_tools' && Array.isArray(value)) {
    const tools = value
      .map((item) => {
        if (!item || typeof item !== 'object') return null
        const tool = typeof item.tool === 'string' ? item.tool : ''
        const count = typeof item.count === 'number' ? item.count : null
        if (!tool) return null
        return count != null ? `${tool} (${count})` : tool
      })
      .filter(Boolean)
    return tools.length ? `top tools: ${tools.join(', ')}` : null
  }
  if (key === 'usage_totals' && typeof value === 'object' && !Array.isArray(value)) {
    const totalTokens =
      typeof value.total_tokens === 'number'
        ? value.total_tokens
        : typeof value.prompt_tokens === 'number' || typeof value.completion_tokens === 'number'
          ? [value.prompt_tokens, value.completion_tokens].filter((item) => typeof item === 'number').reduce((sum, item) => sum + item, 0)
          : null
    if (totalTokens == null) return null
    const parts = [t('agentRun.tokens', { total: totalTokens })]
    if (typeof value.prompt_tokens === 'number') parts.push(`prompt ${value.prompt_tokens}`)
    if (typeof value.completion_tokens === 'number') parts.push(`completion ${value.completion_tokens}`)
    return parts.join(' · ')
  }
  return null
}

function describeStep(step, t) {
  const input = step?.input || {}
  const output = step?.output || {}

  switch (step?.step_name) {
    case 'run_started':
      return {
        title: t('agentRun.step.runStarted.title'),
        description:
          input.user_request || input.topic_definition || t('agentRun.step.runStarted.description'),
      }
    case 'graph_invoke': {
      const count = output.selected_candidates
      return {
        title: t('agentRun.step.graphInvoke.title'),
        description:
          count != null
            ? t('agentRun.step.graphInvoke.withCount', { count })
            : t('agentRun.step.graphInvoke.description'),
      }
    }
    case 'candidate_saved':
      return {
        title: t('agentRun.step.candidateSaved.title'),
        description:
          output.headline || output.topic || t('agentRun.step.candidateSaved.description'),
      }
    case 'embedding_duplicate_detected': {
      const score = output.similarity_score
      return {
        title: t('agentRun.step.duplicate.title'),
        description:
          score != null
            ? t('agentRun.step.duplicate.withScore', { score: Math.round(Number(score) * 100) })
            : t('agentRun.step.duplicate.description'),
      }
    }
    case 'review_item_created':
      return {
        title: t('agentRun.step.reviewCreated.title'),
        description: t('agentRun.step.reviewCreated.description'),
      }
    case 'review_item_auto_resolved':
      return {
        title: t('agentRun.step.autoResolved.title'),
        description:
          output.review_action
            ? t('agentRun.step.autoResolved.withAction', { action: output.review_action })
            : t('agentRun.step.autoResolved.description'),
      }
    case 'auto_publish_guardrail_blocked':
      return {
        title: t('agentRun.step.guardrailBlocked.title'),
        description:
          output.reasons?.length
            ? t('agentRun.step.guardrailBlocked.withReasons', { reasons: output.reasons.join(', ') })
            : t('agentRun.step.guardrailBlocked.description'),
      }
    case 'auto_publish_guardrail_noted':
      return {
        title: t('agentRun.step.guardrailNoted.title'),
        description:
          output.reasons?.length
            ? t('agentRun.step.guardrailNoted.withReasons', { reasons: output.reasons.join(', ') })
            : t('agentRun.step.guardrailNoted.description'),
      }
    case 'candidate_auto_materialized':
      return {
        title: t('agentRun.step.materialized.title'),
        description:
          output.content_item_id
            ? t('agentRun.step.materialized.withContent', { id: output.content_item_id })
            : t('agentRun.step.materialized.description'),
      }
    case 'review_resolved':
      return {
        title: t('agentRun.step.reviewResolved.title'),
        description:
          output.decision ? t('agentRun.step.reviewResolved.withDecision', { decision: t(`review.status.${output.decision}`, { defaultValue: output.decision }) }) : t('agentRun.step.reviewResolved.description'),
      }
    case 'run_completed': {
      const parts = []
      if (output.candidate_count != null) parts.push(t('agentRun.step.completed.candidates', { count: output.candidate_count }))
      if (output.review_count != null) parts.push(t('agentRun.step.completed.reviews', { count: output.review_count }))
      if (output.auto_materialized_count != null) {
        parts.push(t('agentRun.step.completed.autoMaterialized', { count: output.auto_materialized_count }))
      }
      return {
        title: t('agentRun.step.completed.title'),
        description: parts.length ? parts.join(' · ') : t('agentRun.step.completed.description'),
      }
    }
    case 'run_failed':
      return {
        title: t('agentRun.step.failed.title'),
        description:
          formatStepValue(output.error_message) ||
          formatStepValue(input.error_message) ||
          t('agentRun.step.failed.description'),
      }
    default:
      return {
        title: step?.step_name || t('common.step'),
        description: formatStepValue(output.message) || formatStepValue(input.message) || t('agentRun.step.default.description'),
      }
  }
}

export default function AgentRunDetail() {
  const { locale, t } = useI18n()
  const { workspaceId, runId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const [run, setRun] = useState(null)
  const [steps, setSteps] = useState([])
  const [reviewItems, setReviewItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  useEffect(() => {
    if (!workspaceId || !runId) return
    setLoading(true)
    setError('')
    Promise.all([
      getAgentRun(workspaceId, runId),
      listAgentRunSteps(workspaceId, runId),
      listAgentReviewQueue(workspaceId),
    ])
      .then(([runResponse, stepsResponse, reviewResponse]) => {
        setRun(runResponse)
        setSteps(stepsResponse.items || [])
        setReviewItems((reviewResponse.items || []).filter((item) => item.agent_run_id === runId))
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [workspaceId, runId])

  const toolSummaryEntries = useMemo(
    () =>
      Object.entries(run?.tool_summary || {})
        .map(([key, value]) => [key, toolSummaryLabel(key, value, t)])
        .filter(([, value]) => typeof value === 'string' && value.trim()),
    [run]
  )
  const progressSteps = useMemo(
    () =>
      steps.map((step) => ({
        ...step,
        progress: describeStep(step, t),
      })),
    [steps]
  )

  return (
    <AppShell
      title={t('agentRun.title')}
      subtitle={t('agentRun.subtitle')}
      user={user}
      onLogout={handleLogout}
      actions={
        <>
          <Link to={`/workspaces/${workspaceId}/agents/candidates`} className="btn btn-secondary btn-small">
            {t('agents.nav.candidates')}
          </Link>
          <Link to={`/workspaces/${workspaceId}/content`} className="btn btn-secondary btn-small">
            {t('common.toPosts')}
          </Link>
        </>
      }
    >
      <AgentSectionLayout workspaceId={workspaceId} activeItem={agentNavKey(run?.graph_name || run?.mode)}>
      {error && <p className="error">{error}</p>}
      {loading && (
        <div className="card">
          <p className="muted">{t('common.loading')}</p>
        </div>
      )}

      {!loading && run && (
        <div style={{ display: 'grid', gap: '1rem' }}>
          <div className="card" style={{ display: 'grid', gap: '0.75rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
              <div>
                <h2 style={{ margin: 0 }}>{agentModeLabel(run.graph_name || run.mode, t)}</h2>
                <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                  {t('common.status')}: {t(`review.status.${run.status}`, { defaultValue: run.status })}
                </p>
              </div>
              <div className="muted" style={{ textAlign: 'right' }}>
                <div>{t('agentRun.created')}: {formatDate(run.created_at, locale)}</div>
                <div>{t('agentRun.completed')}: {formatDate(run.completed_at, locale)}</div>
                <div>{t('agentRun.duration')}: {run.duration_ms != null ? `${run.duration_ms} ms` : '—'}</div>
              </div>
            </div>

            {run.user_request && (
              <div>
                <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('agentRun.request')}</p>
                <p style={{ margin: 0 }}>{run.user_request}</p>
              </div>
            )}

            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <span className="muted">{t('agentRun.model')}: {run.model || '—'}</span>
              <span className="muted">{t('agentRun.provider')}: {run.provider_type || '—'}</span>
              <span className="muted">{t('agentRun.trace')}: {run.trace_policy || '—'}</span>
              <span className="muted">{t('agentRun.channel')}: {run.saas_channel_id || run.channel_id || '—'}</span>
            </div>

            {!!toolSummaryEntries.length && (
              <div>
                <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('agentRun.tools')}</p>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                  {toolSummaryEntries.map(([tool, label]) => (
                    <span
                      key={tool}
                      style={{
                        padding: '0.2rem 0.5rem',
                        borderRadius: 999,
                        background: 'var(--surface-strong)',
                        fontSize: '0.85rem',
                      }}
                    >
                      {label}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>{t('agentRun.progress.title')}</h3>
            {progressSteps.length === 0 && <p className="muted">{t('agentRun.progress.empty')}</p>}
            {progressSteps.length > 0 && (
              <div style={{ display: 'grid', gap: '0.75rem' }}>
                {progressSteps.map((step) => (
                  <div
                    key={`progress-${step.id}`}
                    style={{
                      padding: '0.9rem 1rem',
                      border: '1px solid var(--border)',
                      borderRadius: '14px',
                      background: 'var(--surface-strong)',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        gap: '1rem',
                        flexWrap: 'wrap',
                        marginBottom: '0.35rem',
                      }}
                    >
                      <strong>{step.progress.title}</strong>
                      <span className="muted">
                        {t(`review.status.${step.status}`, { defaultValue: step.status })}
                        {step.duration_ms != null ? ` · ${step.duration_ms} ms` : ''}
                      </span>
                    </div>
                    <p style={{ margin: 0 }}>{step.progress.description}</p>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>{t('agentRun.steps.title')}</h3>
            {steps.length === 0 && <p className="muted">{t('agentRun.steps.empty')}</p>}
            {steps.length > 0 && (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {steps.map((step) => (
                  <li
                    key={step.id}
                    style={{
                      padding: '0.9rem 0',
                      borderBottom: '1px solid var(--border)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
                      <strong>{step.step_name}</strong>
                      <span className="muted">
                        {t(`review.status.${step.status}`, { defaultValue: step.status })}
                        {step.duration_ms != null ? ` · ${step.duration_ms} ms` : ''}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>{t('agentRun.reviewItems.title')}</h3>
            {reviewItems.length === 0 && <p className="muted">{t('agentRun.reviewItems.empty')}</p>}
            {reviewItems.length > 0 && (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {reviewItems.map((item) => (
                  <li
                    key={item.id}
                    style={{
                      padding: '0.9rem 0',
                      borderBottom: '1px solid var(--border)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
                      <div>
                        <strong>{item.review_payload?.headline || item.review_payload?.topic || item.id}</strong>
                        <p className="muted" style={{ margin: '0.3rem 0 0 0' }}>
                          {item.review_payload?.summary || t('common.noDescription')}
                        </p>
                      </div>
                      <div style={{ textAlign: 'right' }}>
                        <div className="muted">{t(`review.status.${item.status}`, { defaultValue: item.status })}</div>
                        <Link
                          to={`/workspaces/${workspaceId}/agents/candidates/${item.id}`}
                          className="btn btn-secondary btn-small"
                          style={{ marginTop: '0.5rem' }}
                        >
                          {t('reviewQueue.openCandidate')}
                        </Link>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
      </AgentSectionLayout>
    </AppShell>
  )
}
