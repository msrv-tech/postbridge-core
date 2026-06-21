import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { askSupportAssistant } from '../adapters/supportAssistant'
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
  if (pathname.includes('/channels')) return 'channels'
  if (pathname.includes('/content')) return 'content'
  if (pathname.includes('/agents/')) return 'agents'
  if (pathname.includes('/settings')) return 'settings'
  return 'workspace'
}

function assistantActionLabel(t, action) {
  if (action === 'add_channel') return t('assistant.action.addChannel')
  if (action === 'create_bridge') return t('assistant.action.createBridge')
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
  const { locale, t } = useI18n()
  const widgetRef = useRef(null)
  const [hidden, setHidden] = useState(() => isSupportAssistantHidden(workspaceId))
  const [collapsed, setCollapsed] = useState(false)
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
  }, [workspaceId])

  useEffect(() => {
    const handleVisibility = (event) => {
      if (event.detail?.workspaceId && event.detail.workspaceId !== workspaceId) return
      setHidden(isSupportAssistantHidden(workspaceId))
    }
    window.addEventListener(supportAssistantVisibilityEvent, handleVisibility)
    return () => window.removeEventListener(supportAssistantVisibilityEvent, handleVisibility)
  }, [workspaceId])

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

  if (!workspaceId || isSelfhostMode() || hidden) return null

  const askAssistant = async (rawQuestion) => {
    const text = String(rawQuestion || '').trim() || t('assistant.defaultQuestion')
    if (loading) return
    setCollapsed(false)
    setLoading(true)
    setError('')
    try {
      const response = await askSupportAssistant(workspaceId, {
        page,
        question: text,
        conversation_id: conversationId || undefined,
        client_context: {
          route: `${location.pathname}${location.search}`,
          locale,
        },
      })
      setAnswer(response)
      setConversationId(response.conversation_id || conversationId)
      reachMetrikaGoal('support_assistant_answered', {
        workspace_id: workspaceId,
        page,
        next_action: response.next_action || 'none',
      })
    } catch (e) {
      setError(e.message || t('assistant.failed'))
    } finally {
      setLoading(false)
    }
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
          {error && <p className="error">{error}</p>}
        </div>
        <form className="support-assistant-form" onSubmit={submitQuestion}>
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
