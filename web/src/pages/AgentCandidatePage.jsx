import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { clearToken } from '../adapters/sessionToken'
import {
  getAgentCandidate,
  getAgentReviewItem,
  resolveAgentReviewItem,
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

function scoreValue(value) {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

function buildSelectionKey(url, fallback) {
  const value = typeof url === 'string' ? url.trim() : ''
  return value || fallback
}

function CandidateDetail({ candidate }) {
  const { t } = useI18n()
  if (!candidate) {
    return (
      <div className="card">
        <p className="muted">{t('candidate.notLoaded')}</p>
      </div>
    )
  }
  const scores = candidate.scores || {}
  const sourceBundle = candidate.source_bundle || {}
  const conflictExamples = sourceBundle.conflict_explanations || []
  const reviewHints = candidate.review_hints || []
  const topicAngles = sourceBundle.topic_angles || []
  return (
    <div className="card" style={{ display: 'grid', gap: '1rem' }}>
      <div>
        <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('common.topic')}</p>
        <h2 style={{ margin: 0 }}>{candidate.headline || candidate.topic || t('common.untitled')}</h2>
        {candidate.summary && <p style={{ marginTop: '0.5rem' }}>{candidate.summary}</p>}
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span className="muted">{t('candidate.score.quality', { value: scoreValue(scores.source_quality) })}</span>
        <span className="muted">{t('candidate.score.freshness', { value: scoreValue(scores.source_freshness) })}</span>
        <span className="muted">{t('candidate.score.conflict', { value: scoreValue(scores.source_conflict) })}</span>
        <span className="muted">{t('candidate.score.ranking', { value: scoreValue(scores.rerank_combined || scores.rerank) })}</span>
      </div>

      {candidate.why_now && (
        <div>
          <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('candidate.whyNow')}</p>
          <p style={{ margin: 0 }}>{candidate.why_now}</p>
        </div>
      )}

      {candidate.dedup_summary && (
        <div>
          <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('candidate.dedup')}</p>
          <p style={{ margin: 0 }}>{candidate.dedup_summary}</p>
        </div>
      )}

      {candidate.style_fit_summary && (
        <div>
          <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('candidate.styleFit')}</p>
          <p style={{ margin: 0 }}>{candidate.style_fit_summary}</p>
        </div>
      )}

      {!!candidate.risk_flags?.length && (
        <div>
          <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('candidate.risks')}</p>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {candidate.risk_flags.map((flag) => (
              <span
                key={flag}
                style={{
                  padding: '0.2rem 0.5rem',
                  borderRadius: 999,
                  background: 'var(--surface-strong)',
                  fontSize: '0.85rem',
                }}
              >
                {flag}
              </span>
            ))}
          </div>
        </div>
      )}

      {!!reviewHints.length && (
        <div>
          <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('candidate.reviewHints')}</p>
          <ul style={{ margin: 0, paddingLeft: '1rem' }}>
            {reviewHints.map((hint, index) => (
              <li key={`${hint}-${index}`}>{hint}</li>
            ))}
          </ul>
        </div>
      )}

      {!!topicAngles.length && (
        <div>
          <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('candidate.topicAngles')}</p>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {topicAngles.map((angle, index) => (
              <span
                key={`${angle.angle || angle.label || index}`}
                style={{
                  padding: '0.2rem 0.5rem',
                  borderRadius: 999,
                  background: 'var(--surface-strong)',
                  fontSize: '0.85rem',
                }}
              >
                {angle.angle || angle.label || t('candidate.angleFallback')}
              </span>
            ))}
          </div>
        </div>
      )}

      {!!conflictExamples.length && (
        <div>
          <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('candidate.sourceConflict')}</p>
          <ul style={{ margin: 0, paddingLeft: '1rem' }}>
            {conflictExamples.map((item, index) => (
              <li key={`${item.reason || 'conflict'}-${index}`}>
                {(item.left_title || item.left_source || t('candidate.source1'))} vs {(item.right_title || item.right_source || t('candidate.source2'))}
                {item.reason ? `: ${item.reason}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <p className="muted" style={{ marginBottom: '0.25rem' }}>{t('common.draft')}</p>
        <pre
          style={{
            whiteSpace: 'pre-wrap',
            margin: 0,
            padding: '1rem',
            borderRadius: '12px',
            background: 'var(--surface-strong)',
            maxHeight: '24rem',
            overflow: 'auto',
          }}
        >
          {candidate.body_markdown || '—'}
        </pre>
      </div>
    </div>
  )
}

export default function AgentCandidatePage() {
  const { locale, t } = useI18n()
  const { workspaceId, reviewItemId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const [item, setItem] = useState(null)
  const [candidate, setCandidate] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [decisionNote, setDecisionNote] = useState('')
  const [reviewAction, setReviewAction] = useState('')
  const [resolving, setResolving] = useState(false)
  const [imageSelections, setImageSelections] = useState({})

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  useEffect(() => {
    if (!workspaceId || !reviewItemId) return
    setLoading(true)
    setError('')
    setNotice('')
    Promise.all([
      getAgentReviewItem(workspaceId, reviewItemId),
    ])
      .then(async ([reviewItem]) => {
        setItem(reviewItem)
        if (reviewItem?.candidate_id) {
          const candidateResponse = await getAgentCandidate(workspaceId, reviewItem.candidate_id)
          setCandidate(candidateResponse)
        } else {
          setCandidate(null)
        }
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [workspaceId, reviewItemId])

  const currentImageCandidates = useMemo(() => {
    const fromCandidate = candidate?.source_bundle?.image_candidates
    if (Array.isArray(fromCandidate) && fromCandidate.length) return fromCandidate
    const fromPayload = item?.review_payload?.source_bundle?.image_candidates
    return Array.isArray(fromPayload) ? fromPayload : []
  }, [candidate, item])

  const reviewKind = item?.review_payload?.kind || null
  const isSourcePackage = reviewKind === 'source_package'
  const contentItemId = item?.materialization?.content_item_id || item?.review_payload?.content_item_id || null
  const followUpRunId = item?.follow_up_run?.agent_run_id || item?.decision?.follow_up_run_id || null
  const hasAutomaticNextStep = Boolean(contentItemId || followUpRunId)
  const approveButtonLabel = isSourcePackage ? t('candidate.actions.continueWithSources') : t('candidate.actions.approveOpenDraft')
  const actionLabel = isSourcePackage ? t('candidate.nextStep') : t('candidate.editorDecision')

  const isImageSelected = (url, fallbackKey) => {
    const key = buildSelectionKey(url, fallbackKey)
    const selected = imageSelections[reviewItemId]
    return Array.isArray(selected) ? selected.includes(key) : false
  }

  const handleToggleImageSelection = (url, checked, fallbackKey) => {
    const key = buildSelectionKey(url, fallbackKey)
    setImageSelections((prev) => {
      const current = Array.isArray(prev[reviewItemId]) ? prev[reviewItemId] : []
      const next = checked ? Array.from(new Set([...current, key])) : current.filter((value) => value !== key)
      return { ...prev, [reviewItemId]: next }
    })
  }

  const handleResolve = async (decision) => {
    if (!workspaceId || !reviewItemId) return
    setResolving(true)
    setError('')
    setNotice('')
    try {
      const approvedImageUrls = currentImageCandidates
        .map((image, index) => ({ url: image?.url, index }))
        .filter((item) => typeof item.url === 'string' && item.url.trim())
        .filter((item) => isImageSelected(item.url, `image-${item.index}`))
        .map((item) => item.url.trim())
      const resolved = await resolveAgentReviewItem(workspaceId, reviewItemId, {
        decision,
        note: decisionNote.trim() || null,
        review_action: reviewAction.trim() || null,
        approved_image_urls: approvedImageUrls,
      })
      setItem(resolved)
      if (decision === 'approved') {
        const nextContentItemId =
          resolved?.materialization?.content_item_id ||
          resolved?.review_payload?.content_item_id
        if (nextContentItemId) {
          navigate(`/workspaces/${workspaceId}/content/${nextContentItemId}?fromReviewQueue=1`)
          return
        }
        const nextFollowUpRunId =
          resolved?.follow_up_run?.agent_run_id ||
          resolved?.decision?.follow_up_run_id
        if (nextFollowUpRunId && nextFollowUpRunId !== 'pending') {
          navigate(`/workspaces/${workspaceId}/agents/runs/${nextFollowUpRunId}`)
          return
        }
        if (isSourcePackage) {
          setNotice(t('candidate.notice.sourcesApproved'))
        } else {
          setNotice(t('candidate.notice.approvedNoDraft'))
        }
      } else {
        setNotice(t('candidate.notice.rejected'))
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setResolving(false)
    }
  }

  return (
    <AppShell
      title={t('candidate.title')}
      subtitle={t('candidate.subtitle')}
      user={user}
      onLogout={handleLogout}
      actions={
        <>
          <Link to={`/workspaces/${workspaceId}/agents/candidates`} className="btn btn-secondary btn-small">
            {t('candidate.toQueue')}
          </Link>
          {item?.agent_run_id ? (
            <Link to={`/workspaces/${workspaceId}/agents/runs/${item.agent_run_id}`} className="btn btn-secondary btn-small">
              {t('candidate.toRun')}
            </Link>
          ) : null}
          <Link to={`/workspaces/${workspaceId}/content`} className="btn btn-secondary btn-small">
            {t('common.toPosts')}
          </Link>
        </>
      }
    >
      <AgentSectionLayout workspaceId={workspaceId} activeItem="candidates">
      {notice && <p className="muted">{notice}</p>}
      {error && <p className="error">{error}</p>}
      {loading && (
        <div className="card">
          <p className="muted">{t('common.loading')}</p>
        </div>
      )}

      {!loading && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 420px) minmax(0, 1fr)', gap: '1rem' }}>
          <div className="card" style={{ display: 'grid', gap: '1rem', alignContent: 'start' }}>
            <div>
              <h2 style={{ margin: 0 }}>{t('candidate.cardTitle')}</h2>
              <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                {t('common.status')}: {t(`review.status.${item?.status}`, { defaultValue: item?.status || '—' })}
              </p>
            </div>
            <div>
              <div className="muted">{t('candidate.created')}</div>
              <div>{formatDate(item?.created_at, locale)}</div>
            </div>
            {item?.review_payload?.topic ? (
              <div>
                <div className="muted">{t('common.topic')}</div>
                <div>{item.review_payload.topic}</div>
              </div>
            ) : null}
            <div>
              <div className="muted">{t('candidate.autonomyMode')}</div>
              <div>{t(`topicScout.autonomy.${item?.review_payload?.autonomy_mode === 'guarded_auto_publish' ? 'auto' : 'approval'}`, { defaultValue: item?.review_payload?.autonomy_mode || '—' })}</div>
            </div>

            {item?.status === 'approved' && !hasAutomaticNextStep && (
              <div
                style={{
                  padding: '0.85rem',
                  borderRadius: '12px',
                  border: '1px solid var(--border)',
                  background: 'var(--surface-strong)',
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: '0.35rem' }}>
                  {isSourcePackage ? t('candidate.sourcesApproved') : t('candidate.draftNotCreated')}
                </div>
                <div className="muted">
                  {isSourcePackage
                    ? t('candidate.sourcesApprovedText')
                    : t('candidate.draftNotCreatedText')}
                </div>
              </div>
            )}

            {item?.status === 'approved' && followUpRunId && followUpRunId !== 'pending' && (
              <div
                style={{
                  padding: '0.85rem',
                  borderRadius: '12px',
                  border: '1px solid var(--border)',
                  background: 'var(--surface-strong)',
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: '0.35rem' }}>{t('candidate.nextStep')}</div>
                <div className="muted" style={{ marginBottom: '0.75rem' }}>
                  {isSourcePackage
                    ? t('candidate.followUp.sourcesText')
                    : t('candidate.followUp.candidateText')}
                </div>
                <Link to={`/workspaces/${workspaceId}/agents/runs/${followUpRunId}`} className="btn btn-secondary btn-small">
                  {t('candidate.followUp.open')}
                </Link>
              </div>
            )}

            {item?.status === 'approved' && contentItemId && (
              <div
                style={{
                  padding: '0.85rem',
                  borderRadius: '12px',
                  border: '1px solid var(--border)',
                  background: 'var(--surface-strong)',
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: '0.35rem' }}>{t('candidate.nextStep')}</div>
                <div className="muted" style={{ marginBottom: '0.75rem' }}>
                  {t('candidate.materializedText')}
                </div>
                <Link to={`/workspaces/${workspaceId}/content/${contentItemId}?fromReviewQueue=1`} className="btn btn-secondary btn-small">
                  {t('candidate.openPost')}
                </Link>
              </div>
            )}

            {item?.status === 'pending' && (
              <>
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label htmlFor="review-action">{actionLabel}</label>
                  <input
                    id="review-action"
                    className="form-control"
                    value={reviewAction}
                    onChange={(e) => setReviewAction(e.target.value)}
                    placeholder={isSourcePackage ? 'continue_with_selected_sources' : 'approve_as_is'}
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label htmlFor="review-note">{t('candidate.note')}</label>
                  <textarea
                    id="review-note"
                    className="form-control"
                    value={decisionNote}
                    onChange={(e) => setDecisionNote(e.target.value)}
                    rows={4}
                    placeholder={t('candidate.notePlaceholder')}
                  />
                </div>

                {currentImageCandidates.length > 0 && (
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label>{t('candidate.publicImages')}</label>
                    <div style={{ display: 'grid', gap: '0.75rem' }}>
                      {currentImageCandidates.map((image, index) => {
                        const url = typeof image?.url === 'string' ? image.url.trim() : ''
                        if (!url) return null
                        return (
                          <label
                            key={url || `${reviewItemId}-${index}`}
                            style={{
                              display: 'grid',
                              gridTemplateColumns: 'auto 96px minmax(0, 1fr)',
                              gap: '0.75rem',
                              alignItems: 'center',
                              padding: '0.75rem',
                              borderRadius: '12px',
                              border: '1px solid var(--border)',
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={isImageSelected(url, `image-${index}`)}
                              onChange={(event) => handleToggleImageSelection(url, event.target.checked, `image-${index}`)}
                              disabled={resolving}
                            />
                            <a href={url} target="_blank" rel="noreferrer">
                              <img
                                src={url}
                                alt={image?.source_title || `image-${index + 1}`}
                                style={{
                                  width: '96px',
                                  height: '72px',
                                  objectFit: 'cover',
                                  borderRadius: '10px',
                                  background: 'var(--surface-strong)',
                                }}
                              />
                            </a>
                            <div style={{ minWidth: 0 }}>
                              <div style={{ fontWeight: 600 }}>{image?.source_title || t('candidate.imageTitle', { number: index + 1 })}</div>
                              {(image?.width || image?.height) && (
                                <div className="muted" style={{ marginTop: '0.15rem' }}>
                                  {image?.width || '—'} x {image?.height || '—'}
                                </div>
                              )}
                              {image?.source_url ? (
                                <a
                                  href={image.source_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="muted"
                                  style={{ display: 'block', overflowWrap: 'anywhere' }}
                                >
                                  {image.source_url}
                                </a>
                              ) : (
                                <span className="muted" style={{ overflowWrap: 'anywhere' }}>{url}</span>
                              )}
                            </div>
                          </label>
                        )
                      })}
                    </div>
                  </div>
                )}

                <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
                  <button type="button" className="btn" disabled={resolving} onClick={() => handleResolve('approved')}>
                    {resolving ? '...' : approveButtonLabel}
                  </button>
                  <button type="button" className="btn btn-secondary" disabled={resolving} onClick={() => handleResolve('rejected')}>
                    {resolving ? '...' : t('candidate.actions.reject')}
                  </button>
                </div>
              </>
            )}
          </div>

          <CandidateDetail candidate={candidate} />
        </div>
      )}
      </AgentSectionLayout>
    </AppShell>
  )
}
