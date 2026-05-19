import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import {
  createAgentEditorMessage,
  createAgentRun,
  getAgentEditorTimeline,
  getPostPlatformPreviews,
  listAgentRunSteps,
  resolveAgentReviewItem,
} from '../adapters/agent'
import { listChannelRegistry } from '../adapters/channels'
import { createContentItem, getContentItem, updateContentItem } from '../adapters/content'
import { getMediaGenerationJob, startMediaGenerationJob, uploadWorkspaceMedia } from '../adapters/media'
import { clearToken } from '../adapters/sessionToken'
import { useAuth } from '../useAuth'
import AppShell from '../components/AppShell'
import { isBillingEnabled } from '../adapters/billing'
import { mdEditorCommandsFilter } from '../mdEditorRu'
import { useI18n } from '../i18n'

import '@uiw/react-md-editor/markdown-editor.css'

const MarkdownEditor = lazy(() => import('@uiw/react-md-editor'))

function UploadIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m17 8-5-5-5 5" />
      <path d="M12 3v12" />
    </svg>
  )
}

function SparklesIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9.5 2.5 11 7l4.5 1.5L11 10l-1.5 4.5L8 10 3.5 8.5 8 7l1.5-4.5Z" />
      <path d="M17 13 18 16l3 1-3 1-1 3-1-3-3-1 3-1 1-3Z" />
    </svg>
  )
}

function EyeIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

function isAgentEditorialChannel(channel) {
  return channel?.platform === 'postbridge'
}

function bridgeAdaptationModeLabel(mode, t) {
  if (mode === 'ai_auto') return t('channels.adaptation.aiAuto')
  if (mode === 'ai_review') return t('channels.adaptation.aiReview')
  return t('channels.adaptation.ruleOnly')
}

function bridgeAdaptationStatusText(item, t) {
  if (item.adaptation_status === 'manual_preview_required') {
    return t('postEditor.preview.status.manualRequired')
  }
  if (item.adaptation_status === 'needs_review') {
    return t('postEditor.preview.status.needsReview')
  }
  if (item.fallback_used) {
    return t('postEditor.preview.status.fallback')
  }
  if (item.adaptation_mode === 'ai_auto') {
    return t('postEditor.preview.status.aiAuto')
  }
  return ''
}

function platformPreviewErrorText(err, t) {
  if (err?.code === 'BILLING_AI_ADAPT_PAID_ONLY') {
    return t('postEditor.preview.error.paidOnly')
  }
  return err?.message || t('postEditor.preview.error.load')
}

function utcIsoToDatetimeLocalInput(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

function snapLocalDatetimeToUtcFiveMinuteIso(localVal) {
  const d = new Date(localVal)
  if (Number.isNaN(d.getTime())) return null
  let t = Date.UTC(
    d.getUTCFullYear(),
    d.getUTCMonth(),
    d.getUTCDate(),
    d.getUTCHours(),
    d.getUTCMinutes(),
    0,
    0,
  )
  const mins = new Date(t).getUTCMinutes()
  if (mins % 5 !== 0) t += (5 - (mins % 5)) * 60 * 1000
  return new Date(t).toISOString()
}

function formatAgentEventTime(value, locale) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(locale, {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function agentRunStatusLabel(value, t) {
  const labels = {
    pending: t('review.status.pending'),
    awaiting_review: t('review.status.awaitingReview'),
    completed: t('review.status.completed'),
    failed: t('review.status.failed'),
  }
  return labels[value] || value || '—'
}

function agentEventLabel(event, t) {
  if (!event) return t('postEditor.event.system')
  if (event.role === 'user') return t('postEditor.event.you')
  if (event.kind === 'error') return t('review.status.failed')
  if (event.role === 'assistant') return t('postEditor.event.response')
  return t('common.step')
}

function agentEventClassName(event) {
  if (!event) return 'system'
  if (event.kind === 'error') return 'error'
  if (event.role === 'user') return 'user'
  if (event.role === 'assistant') return 'assistant'
  return 'system'
}

function agentEventAvatarLabel(event, t) {
  if (!event) return '•'
  if (event.kind === 'error') return '!'
  if (event.role === 'user') return t('postEditor.event.you')
  if (event.role === 'assistant') return 'AI'
  return t('common.step').slice(0, 1)
}

function groupTimelineEvents(events) {
  if (!Array.isArray(events) || events.length === 0) return []
  const groups = []
  for (const event of events) {
    const eventClass = agentEventClassName(event)
    const isCompactSystem = eventClass === 'system'
    const prev = groups[groups.length - 1]
    if (isCompactSystem && prev?.type === 'system-group') {
      prev.events.push(event)
      continue
    }
    if (isCompactSystem) {
      groups.push({
        type: 'system-group',
        key: event.id || `${event.created_at}-${event.content}`,
        events: [event],
      })
      continue
    }
    groups.push({
      type: 'single',
      key: event.id || `${event.created_at}-${event.content}`,
      event,
    })
  }
  return groups
}

function filterDialogEvents(events) {
  if (!Array.isArray(events)) return []
  return events.filter((event) => {
    const eventClass = agentEventClassName(event)
    return eventClass === 'user' || eventClass === 'assistant' || eventClass === 'error'
  })
}

function extractEventReviewItems(event) {
  const reviewItems = event?.payload?.review_items
  return Array.isArray(reviewItems) ? reviewItems.filter((item) => item && typeof item === 'object') : []
}

function isSourcePackageReviewItem(item) {
  return item?.review_payload?.kind === 'source_package'
}

function extractSourcePackageReviewItems(event) {
  return extractEventReviewItems(event).filter(isSourcePackageReviewItem)
}

function sourceLabelFromUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return ''
  try {
    const url = new URL(value)
    return url.hostname.replace(/^www\./, '')
  } catch (_) {
    return value
  }
}

function buildSelectionKey(value, fallback) {
  if (typeof value === 'string' && value.trim()) return value.trim()
  return fallback
}

function extractAutoMaterializedContentItemId(runLike) {
  if (!runLike || typeof runLike !== 'object') return null
  const autoMaterialized = Array.isArray(runLike.auto_materialized)
    ? runLike.auto_materialized
    : Array.isArray(runLike.result?.auto_materialized)
    ? runLike.result.auto_materialized
    : []
  const fromAutoMaterialized =
    autoMaterialized.find((item) => item?.content_item_id)?.content_item_id || null
  if (fromAutoMaterialized) return fromAutoMaterialized
  const fromMaterialization = runLike?.materialization?.content_item_id
  if (typeof fromMaterialization === 'string' && fromMaterialization.trim()) {
    return fromMaterialization
  }
  return null
}

function extractContentItemIdFromSteps(steps) {
  if (!Array.isArray(steps)) return null
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i]
    if (!step || step.step_type !== 'candidate_auto_materialized') continue
    const output = step.output
    if (output && typeof output === 'object') {
      if (typeof output.content_item_id === 'string' && output.content_item_id.trim()) {
        return output.content_item_id
      }
      const nestedContentItemId = output.materialization?.content_item_id
      if (typeof nestedContentItemId === 'string' && nestedContentItemId.trim()) {
        return nestedContentItemId
      }
    }
  }
  return null
}

function formatRunDetailDate(value, locale) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(locale, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function describeRunStep(step, t) {
  const input = step?.input || {}
  const output = step?.output || {}

  switch (step?.step_name) {
    case 'run_started':
      return {
        title: t('agentRun.step.runStarted.title'),
        description: input.user_request || t('agentRun.step.runStarted.description'),
      }
    case 'graph_invoke':
      return {
        title: t('postEditor.step.graphInvoke.title'),
        description:
          output.selected_candidates != null
            ? t('postEditor.step.graphInvoke.withCount', { count: output.selected_candidates })
            : t('agentRun.step.graphInvoke.description'),
      }
    case 'candidate_saved':
      return {
        title: t('postEditor.step.candidateSaved.title'),
        description: output.headline || output.topic || t('postEditor.step.candidateSaved.description'),
      }
    case 'review_item_created':
      return {
        title: t('agentRun.step.reviewCreated.title'),
        description: t('postEditor.step.reviewCreated.description'),
      }
    case 'source_package_review_item_created':
      return {
        title: t('postEditor.step.sourcesReview.title'),
        description:
          output.source_count != null
            ? t('postEditor.step.sourcesReview.withCount', { count: output.source_count })
            : t('postEditor.step.sourcesReview.description'),
      }
    case 'source_package_review_resolved':
      return {
        title: t('postEditor.step.sourcesResolved.title'),
        description:
          output.decision === 'approved'
            ? t('postEditor.step.sourcesResolved.approved')
            : t('postEditor.step.sourcesResolved.rejected'),
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
    case 'run_completed':
      return {
        title: t('agentRun.step.completed.title'),
        description: t('agentRun.step.completed.description'),
      }
    case 'run_failed':
      return {
        title: t('agentRun.step.failed.title'),
        description: output.error_message || input.error_message || t('agentRun.step.failed.description'),
      }
    default:
      return {
        title: step?.step_name || t('common.step'),
        description: output.message || input.message || t('agentRun.step.default.description'),
      }
  }
}

const getCopilotModeOptions = (t) => [
  {
    value: 'guarded_auto_publish',
    label: t('topicScout.autonomy.auto'),
    hint: t('postEditor.agent.mode.autoHint'),
  },
  {
    value: 'draft_approval',
    label: t('topicScout.autonomy.approval'),
    hint: t('postEditor.agent.mode.approvalHint'),
  },
]

function buildPostSnapshot(post) {
  if (!post || typeof post !== 'object') return null
  return {
    id: post.id || '',
    contentMd: post.content_md || '',
    title: post.title || '',
    summary: post.summary || '',
    linkUrl: post.link_url || '',
    cta: post.cta || '',
    tags: Array.isArray(post.tags) ? post.tags.join(', ') : '',
    author: post.author || '',
    coverImageUrl: post.cover_image_url || '',
    status: post.status || 'draft',
    mediaUrl: post.media_url || '',
    scheduleLocal: post.scheduled_publish_at ? utcIsoToDatetimeLocalInput(post.scheduled_publish_at) : '',
    updatedAt: post.updated_at || '',
  }
}

function arePostSnapshotsEqual(left, right) {
  if (!left || !right) return false
  return (
    left.id === right.id &&
    left.contentMd === right.contentMd &&
    left.title === right.title &&
    left.summary === right.summary &&
    left.linkUrl === right.linkUrl &&
    left.cta === right.cta &&
    left.tags === right.tags &&
    left.author === right.author &&
    left.coverImageUrl === right.coverImageUrl &&
    left.status === right.status &&
    left.mediaUrl === right.mediaUrl &&
    left.scheduleLocal === right.scheduleLocal &&
    left.updatedAt === right.updatedAt
  )
}

export default function PostEditor() {
  const { locale, t } = useI18n()
  const { workspaceId, postId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const isEdit = !!postId
  const billingEnabled = isBillingEnabled(user)

  const [contentMd, setContentMd] = useState('')
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [cta, setCta] = useState('')
  const [tags, setTags] = useState('')
  const [author, setAuthor] = useState('')
  const [coverImageUrl, setCoverImageUrl] = useState('')
  const [status, setStatus] = useState('draft')
  const [mediaUrl, setMediaUrl] = useState('')
  const [scheduleLocal, setScheduleLocal] = useState('')
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [copilotChannels, setCopilotChannels] = useState([])
  const [copilotChannelId, setCopilotChannelId] = useState('')
  const [copilotAutonomyMode, setCopilotAutonomyMode] = useState('guarded_auto_publish')
  const [copilotPrompt, setCopilotPrompt] = useState('')
  const [copilotSeedUrls, setCopilotSeedUrls] = useState('')
  const [copilotAdvancedOpen, setCopilotAdvancedOpen] = useState(false)
  const [copilotRunning, setCopilotRunning] = useState(false)
  const [latestAgentRun, setLatestAgentRun] = useState(null)
  const [copilotTimeline, setCopilotTimeline] = useState(null)
  const [copilotTimelineLoading, setCopilotTimelineLoading] = useState(false)
  const [copilotTimelineRefreshing, setCopilotTimelineRefreshing] = useState(false)
  const [copilotRemoteDraftPending, setCopilotRemoteDraftPending] = useState(false)
  const [copilotPendingContentItem, setCopilotPendingContentItem] = useState(null)
  const [copilotViewMode, setCopilotViewMode] = useState('dialog')
  const [copilotRunDetailsOpen, setCopilotRunDetailsOpen] = useState(false)
  const [copilotRunSteps, setCopilotRunSteps] = useState([])
  const [copilotRunStepsLoading, setCopilotRunStepsLoading] = useState(false)
  const [copilotResolvingReviewIds, setCopilotResolvingReviewIds] = useState({})
  const [copilotSourceSelections, setCopilotSourceSelections] = useState({})
  const [platformPreviews, setPlatformPreviews] = useState([])
  const [platformPreviewsLoading, setPlatformPreviewsLoading] = useState(false)
  const [platformPreviewsError, setPlatformPreviewsError] = useState('')
  const [platformPreviewsErrorCode, setPlatformPreviewsErrorCode] = useState('')
  const [bridgeReviewApprovalArmed, setBridgeReviewApprovalArmed] = useState(false)
  const copilotAbortRef = useRef(null)
  const timelineScrollRef = useRef(null)
  const syncedPostSnapshotRef = useRef(null)
  const formStateRef = useRef(null)
  const copilotModeOptions = getCopilotModeOptions(t)
  const selectedCopilotMode =
    copilotModeOptions.find((option) => option.value === copilotAutonomyMode) || copilotModeOptions[0]
  const rawTimelineEvents = Array.isArray(copilotTimeline?.events) ? copilotTimeline.events : []
  const groupedTimelineEvents = groupTimelineEvents(rawTimelineEvents)
  const groupedDialogEvents = groupTimelineEvents(filterDialogEvents(rawTimelineEvents))
  const visibleTimelineGroups = copilotViewMode === 'activity' ? groupedTimelineEvents : groupedDialogEvents
  const hasTimelineEvents = groupedTimelineEvents.length > 0
  const hasDialogEvents = groupedDialogEvents.length > 0
  const hasSingleCopilotChannel = copilotChannels.length === 1
  const activeCopilotChannel = copilotChannels.find((item) => item.id === copilotChannelId) || copilotChannels[0] || null
  const bridgeReviewPreviews = platformPreviews.filter(
    (item) => item.adaptation_status === 'needs_review',
  )
  const manualAiPreviewItems = platformPreviews.filter(
    (item) => item.adaptation_status === 'manual_preview_required',
  )

  const applyPostToForm = (post, { markSynced = true } = {}) => {
    const snapshot = buildPostSnapshot(post)
    if (!snapshot) return
    setContentMd(snapshot.contentMd)
    setTitle(snapshot.title)
    setSummary(snapshot.summary)
    setLinkUrl(snapshot.linkUrl)
    setCta(snapshot.cta)
    setTags(snapshot.tags)
    setAuthor(snapshot.author)
    setCoverImageUrl(snapshot.coverImageUrl)
    setStatus(snapshot.status)
    setMediaUrl(snapshot.mediaUrl)
    setScheduleLocal(snapshot.scheduleLocal)
    if (markSynced) {
      syncedPostSnapshotRef.current = snapshot
      formStateRef.current = snapshot
      setCopilotRemoteDraftPending(false)
      setCopilotPendingContentItem(null)
    }
  }

  const currentFormSnapshot = () => ({
    id: postId || '',
    contentMd,
    title,
    summary,
    linkUrl,
    cta,
    tags,
    author,
    coverImageUrl,
    status,
    mediaUrl,
    scheduleLocal,
    updatedAt: syncedPostSnapshotRef.current?.updatedAt || '',
  })

  const isFormDirty = () => {
    const current = formStateRef.current
    const synced = syncedPostSnapshotRef.current
    if (!current || !synced) return false
    return !arePostSnapshotsEqual(current, synced)
  }

  const applyTimelinePayload = (timeline, { forceApplyContent = false } = {}) => {
    setCopilotTimeline(timeline)
    if (timeline?.latest_run) {
      setLatestAgentRun(timeline.latest_run)
    }
    if (timeline?.content_item?.id !== postId) return
    const incomingSnapshot = buildPostSnapshot(timeline.content_item)
    if (!incomingSnapshot) return
    const syncedSnapshot = syncedPostSnapshotRef.current
    const changedOnServer =
      !syncedSnapshot || !arePostSnapshotsEqual(incomingSnapshot, syncedSnapshot)
    if (!changedOnServer) {
      setCopilotRemoteDraftPending(false)
      setCopilotPendingContentItem(null)
      return
    }
    if (forceApplyContent || !isFormDirty()) {
      applyPostToForm(timeline.content_item, { markSynced: true })
      return
    }
    setCopilotRemoteDraftPending(true)
    setCopilotPendingContentItem(timeline.content_item)
  }

  useEffect(() => {
    if (!isEdit || !workspaceId) return
    setError('')
    setLoading(true)
    getContentItem(workspaceId, postId)
      .then((post) => {
        applyPostToForm(post)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [workspaceId, postId, isEdit])

  useEffect(() => {
    if (!workspaceId) return
    listChannelRegistry(workspaceId)
      .then((response) => {
        const editorialChannels = (response.items || []).filter(isAgentEditorialChannel)
        setCopilotChannels(editorialChannels)
        setCopilotChannelId((prev) => {
          if (prev && editorialChannels.some((item) => item.id === prev)) return prev
          return editorialChannels[0]?.id || ''
        })
      })
      .catch(() => {})
  }, [workspaceId])

  useEffect(() => {
    formStateRef.current = currentFormSnapshot()
    setBridgeReviewApprovalArmed(false)
  }, [postId, contentMd, title, summary, linkUrl, cta, tags, author, coverImageUrl, status, mediaUrl, scheduleLocal])

  const buildPlatformPreviewPayload = (includeAiAdaptation = false) => ({
    content_md: contentMd,
    title: title.trim() || undefined,
    summary: summary.trim() || undefined,
    link_url: linkUrl.trim() || undefined,
    cta: cta.trim() || undefined,
    content_item_id: postId || undefined,
    include_ai_adaptation: includeAiAdaptation,
  })

  const hasPlatformPreviewContent = () => Boolean(
    contentMd.trim() || title.trim() || summary.trim() || cta.trim() || linkUrl.trim(),
  )

  const loadPlatformPreviews = (includeAiAdaptation = false, options = {}) => {
    if (!workspaceId || !hasPlatformPreviewContent()) {
      setPlatformPreviews([])
      setPlatformPreviewsError('')
      setPlatformPreviewsErrorCode('')
      setPlatformPreviewsLoading(false)
      return Promise.resolve()
    }
    setPlatformPreviewsLoading(true)
    setPlatformPreviewsError('')
    setPlatformPreviewsErrorCode('')
    return getPostPlatformPreviews(
      workspaceId,
      buildPlatformPreviewPayload(includeAiAdaptation),
      options,
    )
      .then((response) => {
        setPlatformPreviews(Array.isArray(response?.items) ? response.items : [])
      })
      .catch((err) => {
        if (options.signal?.aborted) return
        setPlatformPreviews([])
        setPlatformPreviewsError(platformPreviewErrorText(err, t))
        setPlatformPreviewsErrorCode(err?.code || '')
      })
      .finally(() => {
        if (!options.signal?.aborted) {
          setPlatformPreviewsLoading(false)
        }
      })
  }

  useEffect(() => {
    if (!workspaceId) {
      setPlatformPreviews([])
      setPlatformPreviewsError('')
      setPlatformPreviewsErrorCode('')
      setPlatformPreviewsLoading(false)
      return undefined
    }

    if (!hasPlatformPreviewContent()) {
      setPlatformPreviews([])
      setPlatformPreviewsError('')
      setPlatformPreviewsErrorCode('')
      setPlatformPreviewsLoading(false)
      return undefined
    }

    const abortController = new AbortController()
    const timeoutId = window.setTimeout(() => {
      loadPlatformPreviews(false, { signal: abortController.signal })
    }, 350)

    return () => {
      window.clearTimeout(timeoutId)
      abortController.abort()
    }
  }, [workspaceId, contentMd, title, summary, linkUrl, cta, t])

  useEffect(() => {
    if (!workspaceId || !postId) {
      setCopilotTimeline(null)
      setCopilotRemoteDraftPending(false)
      return
    }
    let cancelled = false
    const loadTimeline = async ({ showLoading = false, forceApplyContent = false } = {}) => {
      if (showLoading) {
        setCopilotTimelineLoading(true)
      } else {
        setCopilotTimelineRefreshing(true)
      }
      try {
        const timeline = await getAgentEditorTimeline(workspaceId, postId)
        if (cancelled) return
        applyTimelinePayload(timeline, { forceApplyContent })
      } catch (_) {
        // timeline is best-effort; keep editor usable if unavailable
      } finally {
        if (!cancelled && showLoading) {
          setCopilotTimelineLoading(false)
        }
        if (!cancelled && !showLoading) {
          setCopilotTimelineRefreshing(false)
        }
      }
    }
    loadTimeline({ showLoading: true, forceApplyContent: true })
    const intervalId = window.setInterval(() => {
      loadTimeline({ showLoading: false, forceApplyContent: false })
    }, 5000)
    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [workspaceId, postId])

  useEffect(() => {
    const node = timelineScrollRef.current
    if (!node) return
    node.scrollTop = node.scrollHeight
  }, [copilotTimeline?.events?.length])

  useEffect(() => {
    if (!workspaceId || !latestAgentRun?.id) {
      setCopilotRunSteps([])
      setCopilotRunDetailsOpen(false)
      return
    }
    let cancelled = false
    setCopilotRunStepsLoading(true)
    listAgentRunSteps(workspaceId, latestAgentRun.id)
      .then((response) => {
        if (cancelled) return
        setCopilotRunSteps(response.items || [])
      })
      .catch(() => {
        if (cancelled) return
        setCopilotRunSteps([])
      })
      .finally(() => {
        if (!cancelled) setCopilotRunStepsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [workspaceId, latestAgentRun?.id])

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  const [uploadingImage, setUploadingImage] = useState(false)
  const [uploadingCoverImage, setUploadingCoverImage] = useState(false)
  const [uploadingMediaUrl, setUploadingMediaUrl] = useState(false)
  const [generatingCoverImage, setGeneratingCoverImage] = useState(false)
  const [generatingMediaUrl, setGeneratingMediaUrl] = useState(false)
  const [coverGenerationJobId, setCoverGenerationJobId] = useState('')
  const [mediaGenerationJobId, setMediaGenerationJobId] = useState('')
  const [imageGenerationUpgradeOpen, setImageGenerationUpgradeOpen] = useState(false)
  const coverImageFileInputRef = useRef(null)
  const mediaFileInputRef = useRef(null)

  const handlePaste = async (e) => {
    const items = e.clipboardData?.items
    if (!items) return
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (!file) return
        e.preventDefault()
        setUploadingImage(true)
        setError('')
        try {
          const res = await uploadWorkspaceMedia(workspaceId, file)
          const url = res?.url
          if (url) {
            setContentMd((prev) => prev + (prev ? '\n\n' : '') + `![image](${url})`)
          }
        } catch (err) {
          setError(err.message)
        } finally {
          setUploadingImage(false)
        }
        return
      }
    }
  }

  const handleUrlMediaFileChange = async (e, setUrl, setUploading) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setError('')
    try {
      const res = await uploadWorkspaceMedia(workspaceId, file)
      const url = res?.url
      if (url) {
        setUrl(url)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const handleGenerateImage = async (target, setGenerating) => {
    if (!contentMd.trim() && !title.trim() && !summary.trim()) {
      setError(t('postEditor.errors.imagePromptRequired'))
      return
    }
    setGenerating(true)
    setError('')
    try {
      const job = await startMediaGenerationJob(workspaceId, {
        target,
        title: title.trim() || undefined,
        summary: summary.trim() || undefined,
        content_md: contentMd.trim() || undefined,
        content_item_id: postId || undefined,
      })
      if (target === 'cover') {
        setCoverGenerationJobId(job.id || '')
      } else {
        setMediaGenerationJobId(job.id || '')
      }
    } catch (err) {
      if (err?.code === 'BILLING_IMAGE_GENERATION_PAID_ONLY' && billingEnabled) {
        setImageGenerationUpgradeOpen(true)
      }
      setError(err.message)
      setGenerating(false)
    }
  }

  useEffect(() => {
    if (!workspaceId) return undefined
    const jobs = [
      coverGenerationJobId
        ? { id: coverGenerationJobId, target: 'cover', setUrl: setCoverImageUrl, setGenerating: setGeneratingCoverImage, clear: setCoverGenerationJobId }
        : null,
      mediaGenerationJobId
        ? { id: mediaGenerationJobId, target: 'media', setUrl: setMediaUrl, setGenerating: setGeneratingMediaUrl, clear: setMediaGenerationJobId }
        : null,
    ].filter(Boolean)
    if (jobs.length === 0) return undefined
    let cancelled = false
    const poll = async () => {
      await Promise.all(
        jobs.map(async (item) => {
          try {
            const job = await getMediaGenerationJob(workspaceId, item.id)
            if (cancelled) return
            if (job.status === 'completed') {
              if (job.url) item.setUrl(job.url)
              item.setGenerating(false)
              item.clear('')
            } else if (job.status === 'failed') {
              item.setGenerating(false)
              item.clear('')
              setError(job.error_message || t('postEditor.media.generationFailed'))
            }
          } catch (err) {
            if (!cancelled) {
              item.setGenerating(false)
              item.clear('')
              setError(err.message)
            }
          }
        }),
      )
    }
    poll()
    const interval = setInterval(poll, 5000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [workspaceId, coverGenerationJobId, mediaGenerationJobId, t])

  const openImageGenerationPlanChange = () => {
    if (!billingEnabled) return
    setImageGenerationUpgradeOpen(false)
    navigate(`/workspaces/${workspaceId}/settings?billing=change-plan&plan=pro`)
  }

  const savePost = async (
    targetStatus,
    { redirect = true, allowEmptyDraft = false, bridgeReviewApproved = false } = {},
  ) => {
    const finalStatus = targetStatus ?? status
    if (!contentMd.trim() && !(allowEmptyDraft && finalStatus === 'draft')) {
      setError(t('postEditor.errors.contentRequired'))
      return null
    }
    setError('')
    setSaving(true)
    try {
      const payload = {
        content_md: contentMd.trim(),
        status: finalStatus,
        media_url: mediaUrl.trim() || undefined,
        title: title.trim() || undefined,
        summary: summary.trim() || undefined,
        link_url: linkUrl.trim() || undefined,
        cta: cta.trim() || undefined,
        tags: tags.trim() ? tags.split(',').map((t) => t.trim()).filter(Boolean) : undefined,
        author: author.trim() || undefined,
        cover_image_url: coverImageUrl.trim() || undefined,
      }
      if (finalStatus === 'published' && bridgeReviewApproved) {
        payload.bridge_review_approved = true
      }
      if (finalStatus === 'draft') {
        const trimmed = scheduleLocal.trim()
        if (trimmed) {
          const iso = snapLocalDatetimeToUtcFiveMinuteIso(trimmed)
          if (!iso) {
            setError(t('postEditor.errors.invalidDate'))
            return null
          }
          payload.scheduled_publish_at = iso
        } else if (isEdit) {
          payload.scheduled_publish_at = null
        }
      }
      if (finalStatus === 'published' && isEdit) {
        payload.scheduled_publish_at = null
      }
      let savedPost
      if (isEdit) {
        savedPost = await updateContentItem(workspaceId, postId, payload)
      } else {
        savedPost = await createContentItem(workspaceId, payload)
      }
      if (savedPost) {
        applyPostToForm(savedPost)
      }
      if (redirect) {
        navigate(`/workspaces/${workspaceId}/content`)
      } else if (!isEdit && savedPost?.id) {
        navigate(`/workspaces/${workspaceId}/content/${savedPost.id}`, { replace: true })
      }
      return savedPost
    } catch (e) {
      setError(e.message)
      return null
    } finally {
      setSaving(false)
    }
  }

  const handleSubmit = async (targetStatus) => {
    const needsManualAiPreview =
      targetStatus === 'published' &&
      manualAiPreviewItems.some((item) => item.adaptation_mode === 'ai_review')
    if (needsManualAiPreview) {
      setBridgeReviewApprovalArmed(false)
      setError(t('postEditor.errors.previewFirst'))
      return
    }
    const needsBridgeReview = targetStatus === 'published' && bridgeReviewPreviews.length > 0
    if (needsBridgeReview && !bridgeReviewApprovalArmed) {
      setBridgeReviewApprovalArmed(true)
      setError('')
      return
    }
    await savePost(targetStatus, {
      bridgeReviewApproved: needsBridgeReview && bridgeReviewApprovalArmed,
    })
    if (targetStatus === 'published') {
      setBridgeReviewApprovalArmed(false)
    }
  }

  const handleGenerateAiPlatformPreviews = async () => {
    setBridgeReviewApprovalArmed(false)
    setError('')
    await loadPlatformPreviews(true)
  }

  const handleScheduleClick = async () => {
    if (!scheduleLocal.trim()) {
      setError(t('postEditor.errors.scheduleRequired'))
      return
    }
    setStatus('draft')
    await handleSubmit('draft')
  }

  const handleRunCopilot = async () => {
    if (!workspaceId) return
    if (!copilotChannelId) {
      setError(t('postEditor.errors.agentContextRequired'))
      return
    }
    const hasExistingDraftContent = Boolean(contentMd.trim())
    const hasAgentInstruction = Boolean(copilotPrompt.trim())
    if (!hasExistingDraftContent && !hasAgentInstruction) {
      setError(t('postEditor.errors.agentTaskRequired'))
      return
    }
    setCopilotRunning(true)
    setError('')
    const abortController = new AbortController()
    copilotAbortRef.current = abortController
    try {
      let savedPost = null
      let activeContentItemId = postId || null
      if (hasExistingDraftContent || (!activeContentItemId && hasAgentInstruction)) {
        savedPost = await savePost('draft', {
          redirect: false,
          allowEmptyDraft: !hasExistingDraftContent && hasAgentInstruction,
        })
        if (!savedPost?.id) {
          return
        }
        activeContentItemId = savedPost.id
      }
      const seedUrls = copilotSeedUrls
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean)
      const userRequest =
        copilotPrompt.trim() ||
        t('postEditor.agent.defaultPrompt')
      let run = null
      let normalizedRun = null
      let timelinePayload = null
      if (activeContentItemId) {
        const messageResponse = await createAgentEditorMessage(
          workspaceId,
          activeContentItemId,
          {
            channel_id: copilotChannelId,
            user_request: userRequest,
            autonomy_mode: copilotAutonomyMode,
            seed_urls: seedUrls,
          },
          { signal: abortController.signal },
        )
        run = messageResponse?.run || null
        timelinePayload = messageResponse?.timeline || null
        normalizedRun = run
          ? {
              ...run,
              id: run?.id || run?.agent_run_id || '',
            }
          : null
      } else {
        run = await createAgentRun(workspaceId, {
          channel_id: copilotChannelId,
          mode: 'post_copilot',
          content_item_id: null,
          user_request: userRequest,
          autonomy_mode: copilotAutonomyMode,
          seed_urls: seedUrls,
        }, { signal: abortController.signal })
        normalizedRun = {
          ...run,
          id: run?.id || run?.agent_run_id || '',
        }
      }
      if (!normalizedRun) {
        throw new Error(t('postEditor.errors.agentNoResponse'))
      }
      setCopilotPrompt('')
      setLatestAgentRun(normalizedRun)
      if (timelinePayload) {
        applyTimelinePayload(timelinePayload, { forceApplyContent: true })
      } else {
        let createdContentItemId =
          extractAutoMaterializedContentItemId(run) || extractAutoMaterializedContentItemId(normalizedRun)
        if (!createdContentItemId && normalizedRun?.id && normalizedRun?.status === 'completed') {
          const steps = await listAgentRunSteps(workspaceId, normalizedRun.id)
          createdContentItemId = extractContentItemIdFromSteps(steps?.items || [])
        }
        if (createdContentItemId) {
          const refreshedPost = await getContentItem(workspaceId, createdContentItemId)
          applyPostToForm(refreshedPost)
          navigate(`/workspaces/${workspaceId}/content/${createdContentItemId}`, { replace: true })
        }
      }
    } catch (e) {
      if (e?.name === 'AbortError') {
        setError(t('postEditor.errors.agentStopped'))
      } else {
        setError(e.message)
      }
    } finally {
      copilotAbortRef.current = null
      setCopilotRunning(false)
    }
  }

  const handleStopCopilot = () => {
    copilotAbortRef.current?.abort()
  }

  const handleCopilotPromptKeyDown = (event) => {
    if (event.key !== 'Enter' || event.shiftKey) return
    if (event.nativeEvent?.isComposing) return
    event.preventDefault()
    if (!copilotRunning) {
      handleRunCopilot()
    }
  }

  const handleApplyFreshRemoteDraft = () => {
    if (!copilotPendingContentItem) return
    applyPostToForm(copilotPendingContentItem, { markSynced: true })
  }

  const isSeedUrlSelected = (reviewItemId, url, fallback) => {
    const key = buildSelectionKey(url, fallback)
    const selection = copilotSourceSelections[reviewItemId]?.seedUrls
    if (!selection || !(key in selection)) return true
    return Boolean(selection[key])
  }

  const isImageUrlSelected = (reviewItemId, url, fallback) => {
    const key = buildSelectionKey(url, fallback)
    const selection = copilotSourceSelections[reviewItemId]?.imageUrls
    if (!selection || !(key in selection)) return true
    return Boolean(selection[key])
  }

  const handleToggleSourceSelection = (reviewItemId, url, checked) => {
    const key = buildSelectionKey(url, `seed-${reviewItemId}`)
    setCopilotSourceSelections((prev) => ({
      ...prev,
      [reviewItemId]: {
        ...prev[reviewItemId],
        seedUrls: {
          ...(prev[reviewItemId]?.seedUrls || {}),
          [key]: checked,
        },
      },
    }))
  }

  const handleToggleImageSelection = (reviewItemId, url, checked) => {
    const key = buildSelectionKey(url, `image-${reviewItemId}`)
    setCopilotSourceSelections((prev) => ({
      ...prev,
      [reviewItemId]: {
        ...prev[reviewItemId],
        imageUrls: {
          ...(prev[reviewItemId]?.imageUrls || {}),
          [key]: checked,
        },
      },
    }))
  }

  const handleResolveSourcePackageReview = async (reviewItemId, decision) => {
    if (!workspaceId || !reviewItemId) return
    setCopilotResolvingReviewIds((prev) => ({ ...prev, [reviewItemId]: decision }))
    setError('')
    try {
      const reviewItem = rawTimelineEvents
        .flatMap((event) => extractSourcePackageReviewItems(event))
        .find((item) => item?.id === reviewItemId)
      const payload = reviewItem?.review_payload || {}
      const sourcePackage = payload?.source_package || {}
      const selectedSeedUrls =
        (Array.isArray(sourcePackage?.primary_sources_details) ? sourcePackage.primary_sources_details : [])
          .map((item, index) => ({ url: item?.url, index }))
          .filter((item) => typeof item.url === 'string' && item.url.trim())
          .filter((item) => isSeedUrlSelected(reviewItemId, item.url, `seed-${item.index}`))
          .map((item) => item.url.trim())
      const selectedImageUrls =
        (Array.isArray(sourcePackage?.image_candidates) ? sourcePackage.image_candidates : [])
          .map((item, index) => ({ url: item?.url, index }))
          .filter((item) => typeof item.url === 'string' && item.url.trim())
          .filter((item) => isImageUrlSelected(reviewItemId, item.url, `image-${item.index}`))
          .map((item) => item.url.trim())
      if (decision === 'approved' && selectedSeedUrls.length === 0) {
        setError(t('postEditor.errors.sourceRequired'))
        return
      }
      await resolveAgentReviewItem(workspaceId, reviewItemId, {
        decision,
        review_action: null,
        note: null,
        approved_seed_urls: selectedSeedUrls,
        approved_image_urls: selectedImageUrls,
      })
      if (postId) {
        const timeline = await getAgentEditorTimeline(workspaceId, postId)
        applyTimelinePayload(timeline, { forceApplyContent: false })
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setCopilotResolvingReviewIds((prev) => {
        const next = { ...prev }
        delete next[reviewItemId]
        return next
      })
    }
  }

  const hasScheduledTime = Boolean(scheduleLocal.trim())

  if (loading) {
    return (
      <AppShell title={t('postEditor.title')} user={user} onLogout={handleLogout}>
        <div className="card">
          <p className="muted">{t('common.loading')}</p>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell
      title={isEdit ? t('postEditor.editTitle') : t('postEditor.newTitle')}
      subtitle={t('postEditor.subtitle')}
      user={user}
      onLogout={handleLogout}
      actions={
        <Link to={`/workspaces/${workspaceId}/agents/help`} className="btn btn-secondary btn-small">
          FAQ
        </Link>
      }
    >
      <div className="card post-editor-card">
        <form
          className="post-editor-form"
          onSubmit={(e) => {
            e.preventDefault()
            handleSubmit(status)
          }}
        >
          <div className="post-editor-main">
            <div className="form-group">
              <label htmlFor="title">{t('postEditor.field.title')}</label>
              <input
                id="title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="form-control"
                placeholder={t('postEditor.field.titlePlaceholder')}
              />
            </div>

            <div className="form-group">
              <label htmlFor="content-md">{t('postEditor.field.content')}</label>
              <div data-color-mode="dark" onPaste={handlePaste}>
                <Suspense
                  fallback={
                    <div className="post-editor-md-loading">
                      <p className="muted" style={{ margin: 0 }}>{t('postEditor.markdownLoading')}</p>
                    </div>
                  }
                >
                  <MarkdownEditor
                    id="content-md"
                    value={contentMd}
                    onChange={setContentMd}
                    height={380}
                    preview="live"
                    hideToolbar={false}
                    visibleDragbar
                    commandsFilter={(command) => mdEditorCommandsFilter(command, locale, t)}
                    textareaProps={{ lang: locale }}
                  />
                </Suspense>
              </div>
              <p className="muted post-editor-hint">
                {uploadingImage ? t('postEditor.imageHint.uploading') : t('postEditor.imageHint.ready')}
              </p>
            </div>

            <div className="post-editor-section">
              <h4 className="post-editor-section-title">{t('postEditor.agent.title')}</h4>
              <div className="card post-editor-agent-composer">
                <div className="post-editor-agent-header">
                  <div>
                    <p className="post-editor-agent-title">{t('postEditor.agent.dialogTitle')}</p>
                    <p className="post-editor-agent-subtitle">
                      {t('postEditor.agent.text')}
                    </p>
                  </div>
                </div>

                <div className="post-editor-agent-shell">
                  <div className="post-editor-agent-chat">
                    <div className="post-editor-agent-chatbar">
                      <div className="post-editor-agent-chatbar-main">
                        <span className="post-editor-agent-chatbar-title">{t('postEditor.agent.history')}</span>
                        <span className="post-editor-agent-chatbar-meta">
                          {copilotViewMode === 'activity'
                            ? t('postEditor.agent.groupCount', { count: groupedTimelineEvents.length })
                            : t('postEditor.agent.messageCount', { count: groupedDialogEvents.length })}
                        </span>
                      </div>
                      <div className="post-editor-agent-chatbar-side">
                        <div className="post-editor-agent-view-toggle" role="tablist" aria-label={t('postEditor.agent.historyMode')}>
                          <button
                            type="button"
                            className={`post-editor-agent-view-toggle-btn ${copilotViewMode === 'dialog' ? 'active' : ''}`}
                            onClick={() => setCopilotViewMode('dialog')}
                          >
                            {t('postEditor.agent.dialog')}
                          </button>
                          <button
                            type="button"
                            className={`post-editor-agent-view-toggle-btn ${copilotViewMode === 'activity' ? 'active' : ''}`}
                            onClick={() => setCopilotViewMode('activity')}
                          >
                            {t('postEditor.agent.progress')}
                          </button>
                        </div>
                        {copilotRunning && (
                          <span className="post-editor-agent-chatbar-badge running">{t('postEditor.agent.running')}</span>
                        )}
                      </div>
                    </div>

                    {isEdit && (
                      <div className="post-editor-agent-timeline" ref={timelineScrollRef}>
                        {copilotTimelineLoading && (
                          <div className="post-editor-agent-empty">
                            <p className="muted" style={{ margin: 0 }}>{t('postEditor.agent.loadingHistory')}</p>
                          </div>
                        )}
                        {!copilotTimelineLoading && (!copilotTimeline?.events || copilotTimeline.events.length === 0) && (
                          <div className="post-editor-agent-empty">
                            <p style={{ margin: 0 }}>{t('postEditor.agent.emptyHistory')}</p>
                            <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                              {t('postEditor.agent.emptyHistoryHint')}
                            </p>
                          </div>
                        )}
                        {!copilotTimelineLoading && copilotRemoteDraftPending && (
                          <div className="post-editor-agent-banner">
                            <div>
                              <strong>{t('postEditor.agent.newerDraft')}</strong>
                              <p className="muted" style={{ margin: '0.25rem 0 0 0' }}>
                                {t('postEditor.agent.newerDraftHint')}
                              </p>
                            </div>
                            <button
                              type="button"
                              className="btn btn-secondary btn-small"
                              onClick={handleApplyFreshRemoteDraft}
                            >
                              {t('postEditor.agent.apply')}
                            </button>
                          </div>
                        )}
                        {!copilotTimelineLoading && copilotViewMode === 'dialog' && !hasDialogEvents && hasTimelineEvents && (
                          <div className="post-editor-agent-empty post-editor-agent-empty-quiet">
                            <p style={{ margin: 0 }}>{t('postEditor.agent.emptyDialog')}</p>
                            <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                              {t('postEditor.agent.emptyDialogHint')}
                            </p>
                            <button
                              type="button"
                              className="btn btn-secondary btn-small post-editor-agent-empty-action"
                              onClick={() => setCopilotViewMode('activity')}
                            >
                              {t('postEditor.agent.openProgress')}
                            </button>
                          </div>
                        )}
                        {!copilotTimelineLoading && visibleTimelineGroups.length > 0 && (
                          <div className="post-editor-agent-events">
                            {visibleTimelineGroups.map((group) => {
                              if (group.type === 'system-group') {
                                const firstEvent = group.events[0]
                                return (
                                  <div key={group.key} className="post-editor-agent-group system">
                                    <div className="post-editor-agent-avatar system">{t('common.step').slice(0, 1)}</div>
                                    <div className="post-editor-agent-group-main">
                                      <div className="post-editor-agent-event-meta">
                                        <span>{t('postEditor.agent.steps')}</span>
                                        <span>{formatAgentEventTime(firstEvent?.created_at, locale)}</span>
                                      </div>
                                      <div className="post-editor-agent-event system post-editor-agent-event-cluster">
                                        <div className="post-editor-agent-stepcluster">
                                          {group.events.map((event, index) => (
                                            <div
                                              key={event.id || `${event.created_at}-${index}`}
                                              className="post-editor-agent-stepcluster-item"
                                            >
                                              <span className="post-editor-agent-stepcluster-index">{index + 1}</span>
                                              <div className="post-editor-agent-stepcluster-copy">
                                                <strong>{agentEventLabel(event, t)}</strong>
                                                <p>{event.content}</p>
                                              </div>
                                            </div>
                                          ))}
                                        </div>
                                      </div>
                                    </div>
                                  </div>
                                )
                              }

                              const event = group.event
                              const reviewItems = extractEventReviewItems(event)
                              const sourcePackageReviewItems = extractSourcePackageReviewItems(event)
                              const showRunLink =
                                Boolean(event.agent_run_id) && (event.role === 'assistant' || event.kind === 'error')
                              const showReviewLink = reviewItems.length > 0 && sourcePackageReviewItems.length === 0
                              const eventClass = agentEventClassName(event)
                              return (
                                <div
                                  key={group.key}
                                  className={`post-editor-agent-group ${eventClass}`}
                                >
                                  <div className={`post-editor-agent-avatar ${eventClass}`}>
                                    {agentEventAvatarLabel(event, t)}
                                  </div>
                                  <div className="post-editor-agent-group-main">
                                    <div className="post-editor-agent-event-meta">
                                      <span>{agentEventLabel(event, t)}</span>
                                      <span>{formatAgentEventTime(event.created_at, locale)}</span>
                                    </div>
                                    <div className={`post-editor-agent-event ${eventClass}`}>
                                      <div className="post-editor-agent-event-content">{event.content}</div>
                                      {sourcePackageReviewItems.length > 0 && (
                                        <div className="post-editor-agent-source-reviews">
                                          {sourcePackageReviewItems.map((item) => {
                                            const payload = item?.review_payload || {}
                                            const sourcePackage = payload?.source_package || {}
                                            const summary = payload?.source_package_summary || {}
                                            const sources = Array.isArray(sourcePackage?.primary_sources_details)
                                              ? sourcePackage.primary_sources_details
                                              : []
                                            const images = Array.isArray(sourcePackage?.image_candidates)
                                              ? sourcePackage.image_candidates
                                              : []
                                            const pending = item?.status === 'pending'
                                            const resolving = Boolean(copilotResolvingReviewIds[item.id])
                                            const selectedSourceCount = sources.filter((source, index) =>
                                              typeof source?.url === 'string' && source.url.trim()
                                                ? isSeedUrlSelected(item.id, source.url, `seed-${index}`)
                                                : false
                                            ).length
                                            const selectedImageCount = images.filter((image, index) =>
                                              typeof image?.url === 'string' && image.url.trim()
                                                ? isImageUrlSelected(item.id, image.url, `image-${index}`)
                                                : false
                                            ).length
                                            return (
                                              <div key={item.id} className="post-editor-agent-source-card">
                                                <div className="post-editor-agent-source-card-head">
                                                  <strong>{t('postEditor.agent.sourcePackage')}</strong>
                                                  <span>
                                                    {selectedSourceCount}/{summary?.selected_source_count || sources.length} {t('postEditor.agent.sourcesShort')},
                                                    {' '}
                                                    {selectedImageCount}/{summary?.image_candidate_count || images.length} {t('postEditor.agent.imagesShort')}
                                                  </span>
                                                </div>
                                                {sources.length > 0 && (
                                                  <div className="post-editor-agent-source-list">
                                                    {sources.slice(0, 6).map((source, index) => {
                                                      const sourceUrl = typeof source?.url === 'string' ? source.url.trim() : ''
                                                      if (!sourceUrl) return null
                                                      return (
                                                        <label
                                                          key={sourceUrl || `${item.id}-${index}`}
                                                          className="post-editor-agent-source-link post-editor-agent-source-link-selectable"
                                                        >
                                                          <input
                                                            type="checkbox"
                                                            checked={isSeedUrlSelected(item.id, sourceUrl, `seed-${index}`)}
                                                            onChange={(event) =>
                                                              handleToggleSourceSelection(item.id, sourceUrl, event.target.checked)
                                                            }
                                                            disabled={!pending || resolving}
                                                          />
                                                          <a
                                                            href={sourceUrl}
                                                            target="_blank"
                                                            rel="noreferrer"
                                                            className="post-editor-agent-source-link-copy"
                                                          >
                                                            <strong>{source.title || sourceLabelFromUrl(sourceUrl) || t('postEditor.agent.sourceFallback', { number: index + 1 })}</strong>
                                                            <span>{sourceLabelFromUrl(sourceUrl)}</span>
                                                          </a>
                                                        </label>
                                                      )
                                                    })}
                                                  </div>
                                                )}
                                                {images.length > 0 && (
                                                  <div className="post-editor-agent-source-images">
                                                    {images.slice(0, 6).map((image, index) => {
                                                      const imageUrl = typeof image?.url === 'string' ? image.url.trim() : ''
                                                      if (!imageUrl) return null
                                                      return (
                                                        <label
                                                          key={imageUrl || `${item.id}-image-${index}`}
                                                          className="post-editor-agent-source-image-selectable"
                                                        >
                                                          <input
                                                            type="checkbox"
                                                            checked={isImageUrlSelected(item.id, imageUrl, `image-${index}`)}
                                                            onChange={(event) =>
                                                              handleToggleImageSelection(item.id, imageUrl, event.target.checked)
                                                            }
                                                            disabled={!pending || resolving}
                                                          />
                                                          <a
                                                            href={imageUrl}
                                                            target="_blank"
                                                            rel="noreferrer"
                                                            className="post-editor-agent-source-image"
                                                          >
                                                            <img src={imageUrl} alt={image.source_title || `image-${index + 1}`} />
                                                          </a>
                                                        </label>
                                                      )
                                                    })}
                                                  </div>
                                                )}
                                                <div className="post-editor-agent-source-meta">
                                                  <span>
                                                    {t('postEditor.agent.domains', { domains: Array.isArray(summary?.unique_domains) && summary.unique_domains.length > 0
                                                      ? summary.unique_domains.join(', ')
                                                      : '—' })}
                                                  </span>
                                                  <span>
                                                    {t('postEditor.agent.check', { score: typeof summary?.corroboration_score === 'number'
                                                      ? summary.corroboration_score.toFixed(2)
                                                      : '—' })}
                                                  </span>
                                                </div>
                                                {pending && (
                                                  <div className="post-editor-agent-source-actions">
                                                    <button
                                                      type="button"
                                                      className="btn btn-secondary btn-small"
                                                      disabled={resolving}
                                                      onClick={() => handleResolveSourcePackageReview(item.id, 'rejected')}
                                                    >
                                                      {resolving === 'rejected' ? t('postEditor.agent.rejecting') : t('postEditor.agent.reject')}
                                                    </button>
                                                    <button
                                                      type="button"
                                                      className="btn btn-primary btn-small"
                                                      disabled={resolving}
                                                      onClick={() => handleResolveSourcePackageReview(item.id, 'approved')}
                                                    >
                                                      {resolving === 'approved' ? t('postEditor.agent.approving') : t('postEditor.agent.approve')}
                                                    </button>
                                                  </div>
                                                )}
                                              </div>
                                            )
                                          })}
                                        </div>
                                      )}
                                      {(showRunLink || showReviewLink) && (
                                        <div className="post-editor-agent-event-links">
                                          {showRunLink && (
                                            <Link
                                              to={`/workspaces/${workspaceId}/agents/runs/${event.agent_run_id}`}
                                              className="post-editor-agent-inline-link"
                                            >
                                              {t('postEditor.agent.runDetails')}
                                            </Link>
                                          )}
                                          {showReviewLink && (
                                            <Link
                                              to={`/workspaces/${workspaceId}/agents/candidates`}
                                              className="post-editor-agent-inline-link post-editor-agent-inline-link-cta"
                                            >
                                              {t('postEditor.agent.candidates')}
                                            </Link>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )}

                    {!isEdit && (
                      <div className="post-editor-agent-empty">
                        <p className="muted" style={{ margin: 0 }}>
                          {t('postEditor.agent.autosaveHint')}
                        </p>
                      </div>
                    )}

                    <div className="post-editor-agent-body">
                      <textarea
                        id="copilot-prompt"
                        value={copilotPrompt}
                        onChange={(e) => setCopilotPrompt(e.target.value)}
                        onKeyDown={handleCopilotPromptKeyDown}
                        className="form-control post-editor-agent-input"
                        rows={3}
                        placeholder={t('postEditor.agent.taskPlaceholder')}
                        disabled={copilotRunning}
                      />

                      {copilotAdvancedOpen && (
                        <textarea
                          id="copilot-seed-urls"
                          value={copilotSeedUrls}
                          onChange={(e) => setCopilotSeedUrls(e.target.value)}
                          className="form-control post-editor-agent-seeds"
                          rows={2}
                          placeholder={t('postEditor.agent.seedPlaceholder')}
                          disabled={copilotRunning}
                        />
                      )}

                      <div className="post-editor-agent-footer">
                        <div className="post-editor-agent-controls">
                          <button
                            type="button"
                            className="btn btn-secondary btn-small post-editor-agent-plus"
                            onClick={() => setCopilotAdvancedOpen((prev) => !prev)}
                            title={copilotAdvancedOpen ? t('postEditor.agent.hideAdvanced') : t('postEditor.agent.showAdvanced')}
                          >
                            {copilotAdvancedOpen ? '−' : '+'}
                          </button>
                          {hasSingleCopilotChannel ? (
                            <div
                              className="form-control post-editor-agent-select"
                              aria-label={t('postEditor.agent.context')}
                              title={activeCopilotChannel?.platform_channel_id || ''}
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                minWidth: 0,
                                opacity: 0.9,
                              }}
                            >
                              {activeCopilotChannel?.title || activeCopilotChannel?.platform_channel_id || 'Postbridge'}
                            </div>
                          ) : (
                            <select
                              id="copilot-channel"
                              value={copilotChannelId}
                              onChange={(e) => setCopilotChannelId(e.target.value)}
                              className="form-control post-editor-agent-select"
                              disabled={copilotRunning || copilotChannels.length === 0}
                            >
                              {copilotChannels.length === 0 && <option value="">{t('postEditor.agent.noContext')}</option>}
                              {copilotChannels.map((channel) => (
                                <option key={channel.id} value={channel.id}>
                                  {channel.title || channel.platform_channel_id}
                                </option>
                              ))}
                            </select>
                          )}
                          <select
                            id="copilot-autonomy"
                            value={copilotAutonomyMode}
                            onChange={(e) => setCopilotAutonomyMode(e.target.value)}
                            className="form-control post-editor-agent-select"
                            disabled={copilotRunning}
                          >
                            {copilotModeOptions.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </div>
                        {latestAgentRun && (
                          <button
                            type="button"
                            className="btn btn-secondary btn-small post-editor-agent-launch-link"
                            onClick={() => setCopilotRunDetailsOpen((prev) => !prev)}
                          >
                            {copilotRunDetailsOpen ? t('postEditor.agent.hideRun') : t('postEditor.agent.runPanel')}
                          </button>
                        )}
                        {!copilotRunning && (
                          <button
                            type="button"
                            className="btn post-editor-agent-send-icon"
                            onClick={handleRunCopilot}
                            disabled={!copilotChannelId}
                            title={t('postEditor.agent.send')}
                            aria-label={t('postEditor.agent.send')}
                          >
                            ↑
                          </button>
                        )}
                        {copilotRunning && (
                          <button
                            type="button"
                            className="btn btn-secondary post-editor-agent-send-icon"
                            onClick={handleStopCopilot}
                            title={t('postEditor.agent.stop')}
                            aria-label={t('postEditor.agent.stop')}
                          >
                            ■
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                {copilotRunDetailsOpen && latestAgentRun && (
                  <div className="post-editor-agent-runpanel-wrap">
                    <div className="post-editor-agent-runpanel-frame">
                      <div className="post-editor-agent-runpanel">
                        <div className="post-editor-agent-runpanel-header">
                          <div>
                            <p className="post-editor-agent-runpanel-title">{t('postEditor.agent.currentRun')}</p>
                            <p className="post-editor-agent-runpanel-subtitle">
                              {t('postEditor.agent.currentRunHint')}
                            </p>
                          </div>
                          <button
                            type="button"
                            className="btn btn-secondary btn-small"
                            onClick={() => setCopilotRunDetailsOpen(false)}
                          >
                            {t('common.close')}
                          </button>
                        </div>

                        <div className="post-editor-agent-runpanel-meta">
                          <span>{t('postEditor.agent.status', { status: agentRunStatusLabel(latestAgentRun.status, t) })}</span>
                          <span>{t('postEditor.agent.created', { date: formatRunDetailDate(latestAgentRun.created_at, locale) })}</span>
                          <span>{t('postEditor.agent.completed', { date: formatRunDetailDate(latestAgentRun.completed_at, locale) })}</span>
                        </div>

                        {latestAgentRun.user_request && (
                          <div className="post-editor-agent-runpanel-request">
                            {latestAgentRun.user_request}
                          </div>
                        )}

                        <div className="post-editor-agent-runpanel-steps">
                          {copilotRunStepsLoading && (
                            <div className="post-editor-agent-empty">
                              <p className="muted" style={{ margin: 0 }}>{t('postEditor.agent.loadingSteps')}</p>
                            </div>
                          )}
                          {!copilotRunStepsLoading &&
                            copilotRunSteps.map((step) => {
                              const detail = describeRunStep(step, t)
                              return (
                                <div
                                  key={step.id || `${step.step_name}-${step.created_at || ''}`}
                                  className="post-editor-agent-runstep"
                                >
                                  <div className="post-editor-agent-runstep-top">
                                    <strong>{detail.title}</strong>
                                    <span>{formatRunDetailDate(step.created_at, locale)}</span>
                                  </div>
                                  <p>{detail.description}</p>
                                </div>
                              )
                            })}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>

          </div>

          <div className="post-editor-sidebar">
            <div className="post-editor-section">
              <h4 className="post-editor-section-title">{t('postEditor.publication.title')}</h4>
              <div className="form-group">
                <label htmlFor="status">{t('common.status')}</label>
                <select
                  id="status"
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                  className="form-control"
                >
                  <option value="draft">{t('postEditor.status.draft')}</option>
                  <option value="published">{t('postEditor.status.published')}</option>
                </select>
              </div>
              <div className="form-group">
                <label htmlFor="schedule-at">{t('postEditor.schedule.title')}</label>
                <input
                  id="schedule-at"
                  type="datetime-local"
                  value={scheduleLocal}
                  onChange={(e) => setScheduleLocal(e.target.value)}
                  className="form-control"
                />
                <p className="muted post-editor-hint" style={{ marginTop: '0.35rem' }}>
                  {t('postEditor.schedule.hint', { settings: t('settings.title') })}
                </p>
              </div>
            </div>

            <div className="post-editor-section">
              <h4 className="post-editor-section-title">{t('postEditor.previewLinks.title')}</h4>
              <div className="form-group">
                <label htmlFor="summary">{t('postEditor.summary')}</label>
                <textarea
                  id="summary"
                  value={summary}
                  onChange={(e) => setSummary(e.target.value)}
                  className="form-control post-editor-summary"
                  rows={3}
                  placeholder={t('postEditor.summaryPlaceholder')}
                />
              </div>
              <div className="form-group">
                <label htmlFor="link-url">{t('common.link')}</label>
                <input
                  id="link-url"
                  type="url"
                  value={linkUrl}
                  onChange={(e) => setLinkUrl(e.target.value)}
                  className="form-control"
                  placeholder="https://..."
                />
              </div>
              <div className="form-group">
                <label htmlFor="cta">{t('postEditor.cta')}</label>
                <input
                  id="cta"
                  type="text"
                  value={cta}
                  onChange={(e) => setCta(e.target.value)}
                  className="form-control"
                  placeholder={t('postEditor.ctaPlaceholder')}
                />
              </div>
            </div>

            <div className="post-editor-section">
              <h4 className="post-editor-section-title">{t('postEditor.metadata.title')}</h4>
              <div className="form-group">
                <label htmlFor="tags">{t('postEditor.tags')}</label>
                <input
                  id="tags"
                  type="text"
                  value={tags}
                  onChange={(e) => setTags(e.target.value)}
                  className="form-control"
                  placeholder={t('postEditor.tagsPlaceholder')}
                />
              </div>
              <div className="form-group">
                <label htmlFor="author">{t('postEditor.author')}</label>
                <input
                  id="author"
                  type="text"
                  value={author}
                  onChange={(e) => setAuthor(e.target.value)}
                  className="form-control"
                  placeholder={t('postEditor.authorPlaceholder')}
                />
              </div>
            </div>

            <div className="post-editor-section">
              <h4 className="post-editor-section-title">{t('postEditor.media.title')}</h4>
              <div className="form-group">
                <label htmlFor="cover-image-url">{t('postEditor.cover')}</label>
                <div className="post-editor-url-upload-row">
                  <input
                    id="cover-image-url"
                    type="url"
                    value={coverImageUrl}
                    onChange={(e) => setCoverImageUrl(e.target.value)}
                    className="form-control"
                    placeholder={t('postEditor.coverPlaceholder')}
                  />
                  <input
                    ref={coverImageFileInputRef}
                    type="file"
                    accept="image/*"
                    className="post-editor-file-input"
                    onChange={(e) => handleUrlMediaFileChange(e, setCoverImageUrl, setUploadingCoverImage)}
                  />
                  <button
                    type="button"
                    className={`btn btn-secondary btn-small post-editor-url-upload-button${uploadingCoverImage ? ' is-uploading' : ''}`}
                    onClick={() => coverImageFileInputRef.current?.click()}
                    disabled={uploadingCoverImage || generatingCoverImage}
                    aria-label={uploadingCoverImage ? t('postEditor.media.uploading') : t('postEditor.media.upload')}
                    title={uploadingCoverImage ? t('postEditor.media.uploading') : t('postEditor.media.upload')}
                  >
                    <UploadIcon />
                  </button>
                  <button
                    type="button"
                    className={`btn btn-secondary btn-small post-editor-url-upload-button${generatingCoverImage ? ' is-uploading' : ''}`}
                    onClick={() => handleGenerateImage('cover', setGeneratingCoverImage)}
                    disabled={uploadingCoverImage || generatingCoverImage}
                    aria-label={generatingCoverImage ? t('postEditor.media.generating') : t('postEditor.media.generate')}
                    title={generatingCoverImage ? t('postEditor.media.generating') : t('postEditor.media.generate')}
                  >
                    <SparklesIcon />
                  </button>
                  {coverImageUrl.trim() && (
                    <a
                      className="btn btn-secondary btn-small post-editor-url-upload-button"
                      href={coverImageUrl.trim()}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={t('postEditor.media.view')}
                      title={t('postEditor.media.view')}
                    >
                      <EyeIcon />
                    </a>
                  )}
                </div>
              </div>
              <div className="form-group">
                <label htmlFor="media-url">{t('postEditor.mediaUrl')}</label>
                <div className="post-editor-url-upload-row">
                  <input
                    id="media-url"
                    type="url"
                    value={mediaUrl}
                    onChange={(e) => setMediaUrl(e.target.value)}
                    className="form-control"
                    placeholder="https://..."
                  />
                  <input
                    ref={mediaFileInputRef}
                    type="file"
                    accept="image/*"
                    className="post-editor-file-input"
                    onChange={(e) => handleUrlMediaFileChange(e, setMediaUrl, setUploadingMediaUrl)}
                  />
                  <button
                    type="button"
                    className={`btn btn-secondary btn-small post-editor-url-upload-button${uploadingMediaUrl ? ' is-uploading' : ''}`}
                    onClick={() => mediaFileInputRef.current?.click()}
                    disabled={uploadingMediaUrl || generatingMediaUrl}
                    aria-label={uploadingMediaUrl ? t('postEditor.media.uploading') : t('postEditor.media.upload')}
                    title={uploadingMediaUrl ? t('postEditor.media.uploading') : t('postEditor.media.upload')}
                  >
                    <UploadIcon />
                  </button>
                  <button
                    type="button"
                    className={`btn btn-secondary btn-small post-editor-url-upload-button${generatingMediaUrl ? ' is-uploading' : ''}`}
                    onClick={() => handleGenerateImage('media', setGeneratingMediaUrl)}
                    disabled={uploadingMediaUrl || generatingMediaUrl}
                    aria-label={generatingMediaUrl ? t('postEditor.media.generating') : t('postEditor.media.generate')}
                    title={generatingMediaUrl ? t('postEditor.media.generating') : t('postEditor.media.generate')}
                  >
                    <SparklesIcon />
                  </button>
                  {mediaUrl.trim() && (
                    <a
                      className="btn btn-secondary btn-small post-editor-url-upload-button"
                      href={mediaUrl.trim()}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={t('postEditor.media.view')}
                      title={t('postEditor.media.view')}
                    >
                      <EyeIcon />
                    </a>
                  )}
                </div>
              </div>
              <p className="muted post-editor-media-upload-hint">
                {t('postEditor.media.uploadHint')}
              </p>
            </div>

            <div className="post-editor-section">
              <h4 className="post-editor-section-title">{t('postEditor.bridgePreview.title')}</h4>
              {manualAiPreviewItems.length > 0 && (
                <div className="post-editor-platform-preview-actions">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={handleGenerateAiPlatformPreviews}
                    disabled={platformPreviewsLoading}
                  >
                    {platformPreviewsLoading ? t('postEditor.bridgePreview.generating') : t('postEditor.bridgePreview.generate')}
                  </button>
                  <span className="muted">
                    {t('postEditor.bridgePreview.needed', { platforms: manualAiPreviewItems.map((item) => item.platform.toUpperCase()).join(', ') })}
                  </span>
                </div>
              )}
              {platformPreviewsLoading && (
                <p className="muted post-editor-hint">{t('postEditor.bridgePreview.updating')}</p>
              )}
              {!platformPreviewsLoading && platformPreviewsError && (
                <p className="error post-editor-platform-preview-error">
                  {platformPreviewsError}
                  {platformPreviewsErrorCode === 'BILLING_AI_ADAPT_PAID_ONLY' && billingEnabled && (
                    <Link
                      to={`/workspaces/${workspaceId}/settings?billing=change-plan&plan=pro`}
                      className="post-editor-platform-preview-upgrade-link"
                    >
                      {t('common.upgradePlan')}
                    </Link>
                  )}
                </p>
              )}
              {!platformPreviewsLoading && !platformPreviewsError && platformPreviews.length === 0 && (
                <div className="post-editor-review-gate">
                  <strong>{t('postEditor.bridgePreview.noBridgesTitle')}</strong>
                  <p>{t('postEditor.bridgePreview.noBridges')}</p>
                  <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">
                    {t('postEditor.bridgePreview.openChannels')}
                  </Link>
                </div>
              )}
              {platformPreviews.map((item) => {
                const statusText = bridgeAdaptationStatusText(item, t)
                const isReview = item.adaptation_status === 'needs_review'
                return (
                  <div key={item.platform} className="post-editor-platform-preview">
                    <div className="post-editor-platform-preview-head">
                      <strong>{item.platform.toUpperCase()}</strong>
                      <div className="post-editor-platform-preview-badges">
                        <span className="post-editor-platform-preview-mode">
                          {bridgeAdaptationModeLabel(item.adaptation_mode, t)}
                        </span>
                        <span
                          className={`post-editor-platform-preview-badge${
                            item.truncated || isReview || item.fallback_used ? ' warning' : ''
                          }`}
                        >
                          {item.limit ? `${item.adapted_length}/${item.limit}` : `${item.adapted_length}`}
                        </span>
                      </div>
                    </div>
                    <p className="post-editor-platform-preview-targets">
                      {(item.targets || [])
                        .map((target) => target.title || target.platform_channel_id)
                        .join(', ')}
                    </p>
                    {statusText && (
                      <p className="post-editor-platform-preview-status">
                        {statusText}
                      </p>
                    )}
                    {item.truncated && (
                      <p className="post-editor-platform-preview-warning">
                        {t('postEditor.bridgePreview.truncated')}
                      </p>
                    )}
                    <pre className="post-editor-platform-preview-text">{item.text}</pre>
                  </div>
                )
              })}
            </div>
          </div>

          {error && <p className="error post-editor-full-width">{error}</p>}
          {bridgeReviewApprovalArmed && bridgeReviewPreviews.length > 0 && (
            <div className="post-editor-review-gate post-editor-full-width">
              <strong>{t('postEditor.manualReview.title')}</strong>
              <p>
                {t('postEditor.manualReview.text', {
                  platforms: bridgeReviewPreviews.map((item) => item.platform.toUpperCase()).join(', '),
                })}
              </p>
            </div>
          )}

          <div className="post-editor-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => handleSubmit('draft')}
              disabled={saving}
            >
              {saving ? t('common.saving') : t('postEditor.saveDraft')}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleScheduleClick}
              disabled={saving}
            >
              {saving ? t('common.saving') : t('postEditor.schedule')}
            </button>
            {!hasScheduledTime && (
              <button
                type="button"
                className="btn"
                onClick={() => handleSubmit('published')}
                disabled={saving}
              >
                {saving
                  ? t('postEditor.publishing')
                  : bridgeReviewApprovalArmed && bridgeReviewPreviews.length > 0
                    ? t('postEditor.publishChecked')
                    : t('postEditor.publish')}
              </button>
            )}
            <Link to={`/workspaces/${workspaceId}/content`} className="btn btn-secondary">
              {t('common.cancel')}
            </Link>
          </div>
        </form>
        {billingEnabled && imageGenerationUpgradeOpen && (
          <div className="modal-overlay" onClick={() => setImageGenerationUpgradeOpen(false)}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
              <h3>{t('postEditor.media.upgradeTitle')}</h3>
              <p className="muted">{t('postEditor.media.upgradeText')}</p>
              <div className="post-editor-image-upgrade-actions">
                <button
                  type="button"
                  className="btn"
                  onClick={openImageGenerationPlanChange}
                >
                  {t('common.upgradePlan')}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setImageGenerationUpgradeOpen(false)}
                >
                  {t('common.close')}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  )
}
