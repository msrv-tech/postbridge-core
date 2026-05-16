import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { clearToken } from '../adapters/sessionToken'
import { listAgentReviewQueue } from '../adapters/agent'
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

export default function AgentReviewQueue() {
  const { locale, t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  useEffect(() => {
    if (!workspaceId) return
    setLoading(true)
    setError('')
    listAgentReviewQueue(workspaceId)
      .then((response) => {
        setItems(response.items || [])
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [workspaceId])

  return (
    <AppShell
      title={t('reviewQueue.title')}
      subtitle={t('reviewQueue.subtitle')}
      user={user}
      onLogout={handleLogout}
      actions={
        <>
          <Link to={`/workspaces/${workspaceId}/content`} className="btn btn-secondary btn-small">
            {t('common.toPosts')}
          </Link>
        </>
      }
    >
      <AgentSectionLayout workspaceId={workspaceId} activeItem="candidates">
      {error && <p className="error">{error}</p>}
      <div className="card" style={{ minHeight: '30rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h2 style={{ margin: 0 }}>{t('reviewQueue.title')}</h2>
          <button type="button" className="btn btn-secondary btn-small" onClick={() => window.location.reload()}>
            {t('common.refresh')}
          </button>
        </div>
        {loading && <p className="muted">{t('common.loading')}</p>}
        {!loading && items.length === 0 && <p className="muted">{t('reviewQueue.empty')}</p>}
        {!loading && items.length > 0 && (
          <div style={{ display: 'grid', gap: '0.75rem' }}>
            {items.map((item) => {
              const payload = item.review_payload || {}
              const draftText = typeof payload.body_markdown === 'string' ? payload.body_markdown.trim() : ''
              return (
                <div
                  key={item.id}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: '12px',
                    padding: '0.95rem',
                    background: 'transparent',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem' }}>
                    <strong>{payload.headline || payload.topic || t('common.untitled')}</strong>
                    <span className="muted" style={{ fontSize: '0.85rem', whiteSpace: 'nowrap' }}>
                      {t(`review.status.${item.status}`, { defaultValue: item.status })}
                    </span>
                  </div>
                  {payload.summary && (
                    <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                      {String(payload.summary).slice(0, 140)}
                      {String(payload.summary).length > 140 ? '…' : ''}
                    </p>
                  )}
                  <p className="muted" style={{ margin: '0.5rem 0 0 0', fontSize: '0.8rem' }}>
                    {formatDate(item.created_at, locale)}
                  </p>
                  <div style={{ marginTop: '0.85rem' }}>
                    <p className="muted" style={{ marginTop: 0, marginBottom: '0.5rem' }}>{t('common.draft')}</p>
                    <pre
                      style={{
                        whiteSpace: 'pre-wrap',
                        margin: 0,
                        padding: '1rem',
                        borderRadius: '12px',
                        background: 'var(--surface-strong)',
                        maxHeight: '18rem',
                        overflow: 'auto',
                      }}
                    >
                      {draftText || t('reviewQueue.noDraft')}
                    </pre>
                  </div>
                  <div style={{ marginTop: '0.85rem' }}>
                    <Link
                      to={`/workspaces/${workspaceId}/agents/candidates/${item.id}`}
                      className="btn btn-secondary btn-small"
                    >
                      {t('reviewQueue.openCandidate')}
                    </Link>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
      </AgentSectionLayout>
    </AppShell>
  )
}
