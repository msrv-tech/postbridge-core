import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { askSupportAssistant, applyAssistantVideoAction } from '../adapters/supportAssistant'
import {
  isSupportAssistantHidden,
  setSupportAssistantHidden,
  supportAssistantVisibilityEvent,
} from '../adapters/supportAssistantVisibility'
import { isSelfhostMode } from '../adapters/runtime'
import { useI18n } from '../i18n'
import { reachMetrikaGoal } from '../metrika'

function assistantActionRoute(workspaceId, response) {
  if (isSafeWorkspaceRoute(workspaceId, response?.route)) return response.route
  const action = response?.next_action
  if (action === 'add_channel') return `/workspaces/${workspaceId}/channels`
  if (action === 'create_bridge') return `/workspaces/${workspaceId}/migrate`
  if (action === 'open_editor') return `/workspaces/${workspaceId}/content`
  if (action === 'open_import') return `/workspaces/${workspaceId}/channels`
  if (action === 'open_billing') return `/workspaces/${workspaceId}/settings?billing=change-plan`
  if (action === 'open_settings') return `/workspaces/${workspaceId}/settings`
  return ''
}

function isSafeWorkspaceRoute(workspaceId, route) {
  if (!workspaceId || typeof route !== 'string' || !route) return false
  if (route.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(route)) return false
  const prefix = `/workspaces/${workspaceId}`
  return route === prefix || route.startsWith(`${prefix}/`) || route.startsWith(`${prefix}?`)
}

function pageFromPath(pathname) {
  if (pathname.includes('/content/videos') || pathname.includes('/agents/videos')) return 'video'
  if (pathname.includes('/channels')) return 'channels'
  if (pathname.includes('/content')) return 'content'
  if (pathname.includes('/agents/')) return 'agents'
  if (pathname.includes('/settings')) return 'settings'
  return 'workspace'
}

function assistantActionLabel(t, action) {
  if (action === 'add_channel') return t('assistant.action.addChannel')
  if (action === 'create_bridge') return t('assistant.action.createBridge')
  if (action === 'open_video') return t('video.open')
  if (action === 'open_editor') return t('assistant.action.openEditor')
  if (action === 'open_import') return t('assistant.action.openImport')
  if (action === 'open_billing') return t('assistant.action.openBilling')
  if (action === 'open_settings') return t('assistant.action.openSettings')
  return t('assistant.openAction')
}

function positionKey(workspaceId) {
  return `postbridge_support_assistant_position_${workspaceId}`
}

function readPosition(workspaceId) {
  if (typeof window === 'undefined' || !workspaceId) return null
  try {
    const raw = window.localStorage.getItem(positionKey(workspaceId))
    const parsed = raw ? JSON.parse(raw) : null
    if (Number.isFinite(parsed?.x) && Number.isFinite(parsed?.y)) {
      return { x: parsed.x, y: parsed.y }
    }
  } catch {
    // keep default position
  }
  return null
}

function writePosition(workspaceId, position) {
  if (typeof window === 'undefined' || !workspaceId || !position) return
  window.localStorage.setItem(positionKey(workspaceId), JSON.stringify(position))
}

function clampPosition(position, node) {
  const width = node?.offsetWidth || 360
  const height = node?.offsetHeight || 220
  const margin = 12
  return {
    x: Math.max(margin, Math.min(position.x, window.innerWidth - width - margin)),
    y: Math.max(margin, Math.min(position.y, window.innerHeight - height - margin)),
  }
}

export default function SupportAssistantWidget({ workspaceId }) {
  const location = useLocation()
  const navigate = useNavigate()
  const { locale, t } = useI18n()
  const videoProjectId = location.pathname.match(/\/content\/videos\/([0-9a-f-]{36})$/i)?.[1] || ''
  const contextKey = `${workspaceId}/${location.pathname}`
  const contextRef = useRef(contextKey)
  contextRef.current = contextKey
  const widgetRef = useRef(null)
  const requestRef = useRef(null)
  const [hidden, setHidden] = useState(() => isSupportAssistantHidden(workspaceId))
  const [collapsed, setCollapsed] = useState(() => location.pathname.includes('/videos'))
  const [position, setPosition] = useState(() => readPosition(workspaceId))
  const [drag, setDrag] = useState(null)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [conversationId, setConversationId] = useState('')
  const page = useMemo(() => pageFromPath(location.pathname), [location.pathname])

  useEffect(() => {
    setHidden(isSupportAssistantHidden(workspaceId))
    setPosition(readPosition(workspaceId))
    setQuestion('')
    setAnswer(null)
    setError('')
    setConversationId('')
    setLoading(false)
    setCollapsed(page === 'video')
  }, [workspaceId, videoProjectId, page])

  useEffect(() => {
    const handleVisibility = (event) => {
      if (event.detail?.workspaceId && event.detail.workspaceId !== workspaceId) return
      setHidden(isSupportAssistantHidden(workspaceId))
    }
    const collapseForEditor = (event) => {
      if (event.detail?.workspaceId === workspaceId && event.detail?.route === location.pathname) setCollapsed(true)
    }
    window.addEventListener(supportAssistantVisibilityEvent, handleVisibility)
    window.addEventListener('postbridge-assistant-collapse', collapseForEditor)
    return () => {
      window.removeEventListener(supportAssistantVisibilityEvent, handleVisibility)
      window.removeEventListener('postbridge-assistant-collapse', collapseForEditor)
    }
  }, [workspaceId, location.pathname])

  useEffect(() => {
    if (!drag) return undefined
    const handlePointerMove = (event) => {
      const next = clampPosition(
        {
          x: drag.originX + event.clientX - drag.startX,
          y: drag.originY + event.clientY - drag.startY,
        },
        widgetRef.current,
      )
      setPosition(next)
    }
    const handlePointerUp = () => {
      setDrag(null)
      setPosition((current) => {
        if (current) writePosition(workspaceId, current)
        return current
      })
    }
    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
    }
  }, [drag, workspaceId])

  const askAssistant = async (rawQuestion) => {
    const text = String(rawQuestion || '').trim() || t('assistant.defaultQuestion')
    if (loading) return
    const requestContext = contextKey
    setCollapsed(false)
    setLoading(true)
    setError('')
    try {
      const videoContext = { workspaceId, projectId: videoProjectId, hasUnsavedChanges: false }
      window.dispatchEvent(new CustomEvent('postbridge-video-draft-context', { detail: videoContext }))
      const fingerprint = JSON.stringify({ text, workspaceId, contextKey, videoContext })
      if (requestRef.current?.fingerprint !== fingerprint) requestRef.current = { fingerprint, key: crypto.randomUUID() }
      const response = await askSupportAssistant(workspaceId, {
        request_key: requestRef.current.key,
        page,
        question: text,
        conversation_id: conversationId || undefined,
        client_context: {
          route: `${location.pathname}${location.search}`,
          locale,
          video_project_id: videoProjectId || undefined,
          video_has_unsaved_changes: videoContext.hasUnsavedChanges || undefined,
          video_selected_version: videoContext.selectedVersion || undefined,
          video_material_ids: videoContext.materialIds || undefined,
          video_draft_brief: videoContext.brief || undefined,
        },
      })
      if (contextRef.current !== requestContext) return
      requestRef.current = null
      setAnswer(response)
      if (response.video_update?.project_id === videoProjectId) {
        window.dispatchEvent(new CustomEvent('postbridge-video-updated', { detail: { workspaceId, ...response.video_update } }))
      }
      setConversationId(response.conversation_id || conversationId)
      reachMetrikaGoal('support_assistant_answered', {
        workspace_id: workspaceId,
        page,
        next_action: response.next_action || 'none',
      })
    } catch (e) {
      if (contextRef.current === requestContext) setError(e.message || t('assistant.failed'))
    } finally {
      if (contextRef.current === requestContext) setLoading(false)
    }
  }

  useEffect(() => {
    const handleAsk = (event) => {
      if (event.detail?.workspaceId !== workspaceId) return
      if (event.detail?.projectId && event.detail.projectId !== videoProjectId) return
      setSupportAssistantHidden(workspaceId, false)
      setHidden(false)
      setQuestion(event.detail.question || '')
      void askAssistant(event.detail.question)
    }
    window.addEventListener('postbridge-assistant-ask', handleAsk)
    return () => window.removeEventListener('postbridge-assistant-ask', handleAsk)
  }, [workspaceId, location.pathname, locale, conversationId, loading])

  const applyVideoCard = async () => {
    if (loading || !answer?.video_action) return
    const requestContext = contextKey
    setLoading(true); setError('')
    try {
      const result = await applyAssistantVideoAction(workspaceId, answer.video_action.id)
      if (contextRef.current !== requestContext) return
      setAnswer((old) => ({ ...old, content_result: result.content_result, video_action: { ...old.video_action, status: 'applied' } }))
      if (result.video_update) window.dispatchEvent(new CustomEvent('postbridge-video-updated', { detail: { workspaceId, ...result.video_update } }))
      if (isSafeWorkspaceRoute(workspaceId, result.route)) navigate(result.route)
    } catch (e) { if (contextRef.current === requestContext) setError(e.message) }
    finally { if (contextRef.current === requestContext) setLoading(false) }
  }

  const submitQuestion = (event) => {
    event.preventDefault()
    void askAssistant(question)
  }

  const actionRoute = assistantActionRoute(workspaceId, answer)
  const widgetStyle = position ? { left: `${position.x}px`, top: `${position.y}px`, right: 'auto', bottom: 'auto' } : undefined
  const startDrag = (event) => {
    const target = event.target instanceof Element ? event.target : null
    if (event.button !== 0 || target?.closest('button, a, input')) return
    const rect = widgetRef.current?.getBoundingClientRect()
    if (!rect) return
    setDrag({
      startX: event.clientX,
      startY: event.clientY,
      originX: rect.left,
      originY: rect.top,
    })
  }

  if (!workspaceId || isSelfhostMode() || hidden) return null

  if (collapsed) {
    return (
      <div ref={widgetRef} className="support-assistant-widget is-collapsed" style={widgetStyle}>
        <button
          type="button"
          className="support-assistant-launcher"
          onClick={() => setCollapsed(false)}
          aria-label={t('assistant.expand')}
        >
          <span className="support-assistant-launcher-badge">{t('assistant.badgeShort')}</span>
          <span>{t('assistant.title')}</span>
        </button>
      </div>
    )
  }

  return (
    <aside ref={widgetRef} className="support-assistant-widget" style={widgetStyle} aria-label={t('assistant.title')}>
      <div className="support-assistant-window">
        <div className="support-assistant-header" onPointerDown={startDrag}>
          <div>
            <span className="badge badge-running">{t('assistant.badge')}</span>
            <h3>{t('assistant.title')}</h3>
          </div>
          <div className="support-assistant-controls">
            <button
              type="button"
              className="support-assistant-icon-button"
              onClick={() => setCollapsed(true)}
              aria-label={t('assistant.collapse')}
              title={t('assistant.collapse')}
            >
              -
            </button>
            <button
              type="button"
              className="support-assistant-icon-button"
              onClick={() => setSupportAssistantHidden(workspaceId, true)}
              aria-label={t('assistant.hide')}
              title={t('assistant.hide')}
            >
              x
            </button>
          </div>
        </div>
        <div className="support-assistant-body">
          {answer?.message ? (
            <p className="support-assistant-answer">{answer.message}</p>
          ) : (
            <p className="section-copy">{t('assistant.empty')}</p>
          )}
          {answer?.video_update && answer.video_update.project_id !== videoProjectId && <Link className="btn btn-secondary" to={`/workspaces/${workspaceId}/content/videos/${answer.video_update.project_id}`}>{t('video.open')}</Link>}
          {answer?.video_action && <div className="support-assistant-video-card">
            <strong>{t(`videoAssistant.card.${answer.video_action.kind}`)}</strong>
            <p>{answer.video_action.summary.brief}</p>
            {answer.video_action.summary.version && <p>{t('video.version', { version: answer.video_action.summary.version })}</p>}
            {answer.video_action.kind === 'schedule' ? <>
              <p>{t(`topicScout.schedule.${answer.video_action.summary.frequency}`)} · {answer.video_action.summary.time} · {answer.video_action.summary.timezone}</p>
              {answer.video_action.summary.frequency === 'weekly' && <p>{t(`weekday.${['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'][answer.video_action.summary.weekday % 7]}`)}</p>}
              <p>{t('videoSchedule.runBudget')}: {answer.video_action.summary.max_units_per_run || t('videoSchedule.planBudget')}<br />{t('videoSchedule.monthlyBudget')}: {answer.video_action.summary.max_units_per_month || t('videoSchedule.planBudget')}</p>
              <p>{t(`videoAssistant.mode.${answer.video_action.summary.mode || 'autopilot'}`)}</p>
              <p>{t('videoSchedule.draftOnly')}</p>
            </> : answer.video_action.kind === 'schedule_control' ? <p>{t(`videoAssistant.control.${answer.video_action.summary.action}`)} · {answer.video_action.summary.timezone}</p>
              : ['video_generation', 'content_generation'].includes(answer.video_action.kind) ? <>
                {answer.video_action.summary.task && <p>{t(`assistant.content.${answer.video_action.summary.task}`)}</p>}
                {answer.video_action.summary.operation && <p>{t(`assistant.video.${answer.video_action.summary.operation}`)}</p>}
                <p>{t('assistant.content.language')}: {answer.video_action.summary.language}</p>
                {answer.video_action.summary.voice && <p>{answer.video_action.summary.voice}</p>}
                {answer.video_action.summary.scenes?.length > 0 && <details><summary>{t('assistant.video.scenes')}</summary>
                  {answer.video_action.summary.scenes.map((scene, index) => <p key={scene.scene_id || index} className="assistant-content-result">
                    {index + 1}. {scene.caption}{scene.image_prompt ? `\n${scene.image_prompt}` : ''}{scene.narration_text ? `\n${scene.narration_text}` : ''}
                  </p>)}
                </details>}
                {answer.video_action.summary.source && <details><summary>{t('assistant.content.source')}</summary><p className="assistant-content-result">{answer.video_action.summary.source}</p></details>}
                <p>{t('assistant.reserve', { units: answer.video_action.summary.reserve_units })}</p>
                <p className="muted">{t('assistant.reserveHint')}</p>
              </> : <><p>{answer.video_action.summary.caption}</p><p>{t('videoAssistant.draftHint')}</p></>}
            <button type="button" className="btn" disabled={loading || answer.video_action.status === 'applied'} onClick={applyVideoCard}>{t(answer.video_action.status === 'applied' ? 'videoAssistant.applied' : `videoAssistant.apply.${answer.video_action.kind}`)}</button>
          </div>}
          {answer?.content_result && <div className="assistant-content-result" aria-live="polite">
            <p>{answer.content_result.text}</p>
            <p className="muted">{answer.content_result.billing_pending ? t('assistant.billingPending') : t('video.usage', { units: answer.content_result.charged_units })}</p>
          </div>}
          {error && <p className="error">{error}</p>}
        </div>
        <form className="support-assistant-form" onSubmit={submitQuestion}>
          <p className="muted section-copy">{t('assistant.billingHint')}</p>
          <input
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={t('assistant.placeholder')}
            aria-label={t('assistant.questionLabel')}
            disabled={loading}
          />
          <div className="inline-actions support-assistant-actions">
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => askAssistant(t('assistant.defaultQuestion'))}
              disabled={loading}
            >
              {t('assistant.defaultAction')}
            </button>
            <button type="submit" className="btn btn-small" disabled={loading}>
              {loading ? t('assistant.loading') : t('assistant.ask')}
            </button>
            {actionRoute && answer?.next_action !== 'no_action' && (
              <Link to={actionRoute} className="btn btn-secondary btn-small">
                {assistantActionLabel(t, answer.next_action)}
              </Link>
            )}
          </div>
        </form>
      </div>
    </aside>
  )
}
