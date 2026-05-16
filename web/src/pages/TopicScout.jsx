import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { clearToken } from '../adapters/sessionToken'
import { listChannelRegistry } from '../adapters/channels'
import { getDashboardSummary } from '../adapters/dashboard'
import {
  createAgentTask,
  deleteAgentTask,
  listAgentReviewQueue,
  listAgentRuns,
  listAgentTasks,
  pauseAgentTask,
  resumeAgentTask,
  runAgentTask,
} from '../adapters/agent'
import { useAuth } from '../useAuth'
import AppShell from '../components/AppShell'
import AgentSectionLayout from '../components/AgentSectionLayout'
import { isBillingEnabled } from '../adapters/billing'
import { useI18n } from '../i18n'

function isAgentEditorialChannel(channel) {
  return channel?.platform === 'postbridge'
}

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

function getAutonomyModeOptions(t) {
  return [
    {
      value: 'guarded_auto_publish',
      label: t('topicScout.autonomy.auto'),
      hint: t('topicScout.autonomy.autoHint'),
    },
    {
      value: 'draft_approval',
      label: t('topicScout.autonomy.approval'),
      hint: t('topicScout.autonomy.approvalHint'),
    },
  ]
}

const MAX_CANDIDATES_OPTIONS = [1, 2, 3, 4, 5]
const SEARCH_IMAGE_MODES = ['none', 'web_search', 'generate']

function autonomyModeLabel(value, t) {
  const labels = {
    full_manual: t('topicScout.autonomy.approval'),
    draft_approval: t('topicScout.autonomy.approval'),
    plan_approval: t('topicScout.autonomy.approval'),
    guarded_auto_publish: t('topicScout.autonomy.auto'),
  }
  return labels[value] || value || '—'
}

function taskStatusLabel(value, t) {
  const labels = {
    active: t('topicScout.taskStatus.active'),
    paused: t('topicScout.taskStatus.paused'),
    archived: t('topicScout.taskStatus.archived'),
  }
  return labels[value] || value || '—'
}

function getScheduleModeOptions(t) {
  return [
    { value: 'daily', label: t('topicScout.schedule.daily') },
    { value: 'weekdays', label: t('topicScout.schedule.weekdays') },
    { value: 'weekly', label: t('topicScout.schedule.weekly') },
    { value: 'none', label: t('topicScout.schedule.none') },
  ]
}

function getWeekdayOptions(t) {
  return [
    { value: '1', label: t('weekday.monday') },
    { value: '2', label: t('weekday.tuesday') },
    { value: '3', label: t('weekday.wednesday') },
    { value: '4', label: t('weekday.thursday') },
    { value: '5', label: t('weekday.friday') },
    { value: '6', label: t('weekday.saturday') },
    { value: '0', label: t('weekday.sunday') },
  ]
}

function parseTimeParts(value) {
  const match = /^(\d{1,2}):(\d{2})$/.exec((value || '').trim())
  if (!match) return null
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null
  return { hour, minute }
}

function buildScheduleCron({ mode, time, weekday }) {
  if (mode === 'none') return null
  const parts = parseTimeParts(time)
  if (!parts) return null
  const { hour, minute } = parts
  if (mode === 'daily') return `${minute} ${hour} * * *`
  if (mode === 'weekdays') return `${minute} ${hour} * * 1-5`
  if (mode === 'weekly') return `${minute} ${hour} * * ${weekday || '1'}`
  return null
}

function describeSchedule(mode, time, weekday, weekdayOptions, t) {
  if (mode === 'none') return t('topicScout.schedule.description.none')
  if (!parseTimeParts(time)) return t('topicScout.schedule.description.invalidTime')
  if (mode === 'daily') return t('topicScout.schedule.description.daily', { time })
  if (mode === 'weekdays') return t('topicScout.schedule.description.weekdays', { time })
  if (mode === 'weekly') {
    const weekdayLabel = weekdayOptions.find((item) => item.value === weekday)?.label || t('weekday.monday')
    return t('topicScout.schedule.description.weekly', { weekday: weekdayLabel.toLowerCase(), time })
  }
  return ''
}

function humanizeCron(cron, weekdayOptions, t) {
  const value = (cron || '').trim()
  if (!value) return t('topicScout.schedule.noSchedule')
  const daily = /^(\d{1,2}) (\d{1,2}) \* \* \*$/.exec(value)
  if (daily) {
    return t('topicScout.schedule.human.daily', { time: `${String(Number(daily[2])).padStart(2, '0')}:${String(Number(daily[1])).padStart(2, '0')}` })
  }
  const weekdays = /^(\d{1,2}) (\d{1,2}) \* \* 1-5$/.exec(value)
  if (weekdays) {
    return t('topicScout.schedule.human.weekdays', { time: `${String(Number(weekdays[2])).padStart(2, '0')}:${String(Number(weekdays[1])).padStart(2, '0')}` })
  }
  const weekly = /^(\d{1,2}) (\d{1,2}) \* \* ([0-6])$/.exec(value)
  if (weekly) {
    const weekdayLabel = weekdayOptions.find((item) => item.value === weekly[3])?.label?.toLowerCase() || t('topicScout.schedule.scheduled')
    return t('topicScout.schedule.human.weekly', { weekday: weekdayLabel, time: `${String(Number(weekly[2])).padStart(2, '0')}:${String(Number(weekly[1])).padStart(2, '0')}` })
  }
  return `cron: ${value}`
}

export default function TopicScout() {
  const { locale, t } = useI18n()
  const { workspaceId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const { user } = useAuth()
  const resultsRef = useRef(null)
  const createPanelInitializedRef = useRef(false)
  const [channels, setChannels] = useState([])
  const [tasks, setTasks] = useState([])
  const [runs, setRuns] = useState([])
  const [reviewItems, setReviewItems] = useState([])
  const [billingSummary, setBillingSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [runningTaskId, setRunningTaskId] = useState('')
  const [updatingTaskId, setUpdatingTaskId] = useState('')
  const [error, setError] = useState('')

  const [channelId, setChannelId] = useState('')
  const [goalText, setGoalText] = useState('')
  const [editorialInstructions, setEditorialInstructions] = useState('')
  const [scheduleMode, setScheduleMode] = useState('daily')
  const [scheduleTime, setScheduleTime] = useState('07:00')
  const [scheduleWeekday, setScheduleWeekday] = useState('1')
  const [maxCandidatesPerRun, setMaxCandidatesPerRun] = useState(5)
  const [autonomyMode, setAutonomyMode] = useState('guarded_auto_publish')
  const [searchImageMode, setSearchImageMode] = useState('none')
  const [seedUrls, setSeedUrls] = useState('')
  const [isCreateTaskExpanded, setIsCreateTaskExpanded] = useState(true)
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const statusLabels = {
    pending: t('review.status.pending'),
    awaiting_review: t('review.status.awaitingReview'),
    completed: t('review.status.completed'),
    failed: t('review.status.failed'),
    approved: t('review.status.approved'),
    rejected: t('review.status.rejected'),
  }
  const autonomyModeOptions = getAutonomyModeOptions(t)
  const scheduleModeOptions = getScheduleModeOptions(t)
  const weekdayOptions = getWeekdayOptions(t)

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  const loadData = () => {
    if (!workspaceId) return Promise.resolve()
    setLoading(true)
    setError('')
    return Promise.all([
      listChannelRegistry(workspaceId),
      listAgentTasks(workspaceId),
      listAgentRuns(workspaceId),
      listAgentReviewQueue(workspaceId),
      getDashboardSummary(workspaceId),
    ])
      .then(([channelsResponse, tasksResponse, runsResponse, reviewResponse, summaryResponse]) => {
        setBillingSummary(summaryResponse)
        const editorialChannels = (channelsResponse.items || []).filter(isAgentEditorialChannel)
        setChannels(editorialChannels)
        setChannelId((prev) => {
          if (prev && editorialChannels.some((item) => item.id === prev)) return prev
          return editorialChannels[0]?.id || ''
        })
        setTasks((tasksResponse.items || []).filter((item) => item.mode === 'topic_scout'))
        setRuns((runsResponse.items || []).filter((item) => item.graph_name === 'topic_scout'))
        setReviewItems(
          (reviewResponse.items || []).filter(
            (item) =>
              item.review_payload?.mode === 'topic_scout' ||
              item.review_payload?.topic ||
              item.agent_run_id
          )
        )
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadData()
  }, [workspaceId])

  useEffect(() => {
    if (loading || createPanelInitializedRef.current) return
    setIsCreateTaskExpanded(tasks.length === 0)
    createPanelInitializedRef.current = true
  }, [loading, tasks.length])

  useEffect(() => {
    if (tasks.length === 0) {
      setSelectedTaskId('')
      return
    }
    setSelectedTaskId((prev) => (prev && tasks.some((task) => task.id === prev) ? prev : tasks[0].id))
  }, [tasks])

  const selectedTask = useMemo(() => tasks.find((task) => task.id === selectedTaskId) || null, [tasks, selectedTaskId])
  const latestRuns = useMemo(
    () => runs.filter((run) => run.agent_task_id === selectedTaskId).slice(0, 5),
    [runs, selectedTaskId]
  )
  const selectedRunIds = useMemo(() => new Set(latestRuns.map((run) => run.id)), [latestRuns])
  const latestReviewItems = useMemo(
    () => reviewItems.filter((item) => selectedRunIds.has(item.agent_run_id)).slice(0, 5),
    [reviewItems, selectedRunIds]
  )
  const scheduleCron = useMemo(
    () => buildScheduleCron({ mode: scheduleMode, time: scheduleTime, weekday: scheduleWeekday }),
    [scheduleMode, scheduleTime, scheduleWeekday]
  )
  const scheduleDescription = useMemo(
    () => describeSchedule(scheduleMode, scheduleTime, scheduleWeekday, weekdayOptions, t),
    [scheduleMode, scheduleTime, scheduleWeekday, weekdayOptions, t]
  )
  const selectedAutonomyMode =
    autonomyModeOptions.find((option) => option.value === autonomyMode) || autonomyModeOptions[0]
  const billingEnabled = isBillingEnabled(user)
  const isFreePlan = billingSummary?.billing?.plan_code === 'free'
  const canUseAgentSearch = !billingEnabled || !isFreePlan || user?.is_platform_admin
  const upgradeLink = `/workspaces/${workspaceId}/settings?billing=change-plan&plan=pro`

  useEffect(() => {
    if (location.hash !== '#results-table') return
    const node = resultsRef.current
    if (!node) return
    const timer = window.setTimeout(() => {
      node.scrollIntoView({ behavior: 'smooth', block: 'start' })
      node.focus()
    }, 50)
    return () => window.clearTimeout(timer)
  }, [location.hash, latestReviewItems.length])

  const handleCreateTask = async () => {
    if (!workspaceId) return
    if (!canUseAgentSearch) {
      setError(t('topicScout.errors.paidOnly'))
      return
    }
    if (!channelId) {
      setError(t('topicScout.errors.noChannel'))
      return
    }
    if (scheduleMode !== 'none' && !scheduleCron) {
      setError(t('topicScout.errors.invalidSchedule'))
      return
    }
    setSaving(true)
    setError('')
    try {
      await createAgentTask(workspaceId, {
        channel_id: channelId,
        mode: 'topic_scout',
        goal_text: goalText.trim(),
        editorial_instructions: editorialInstructions.trim() || null,
        schedule_cron: scheduleCron.trim() || null,
        max_candidates_per_run: maxCandidatesPerRun,
        autonomy_mode: autonomyMode,
        search_image_mode: searchImageMode,
        seed_urls: seedUrls
          .split('\n')
          .map((item) => item.trim())
          .filter(Boolean),
      })
      await loadData()
      setIsCreateTaskExpanded(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleRunTask = async (taskId) => {
    if (!workspaceId) return
    if (!canUseAgentSearch) {
      setError(t('topicScout.errors.paidOnly'))
      return
    }
    setRunningTaskId(taskId)
    setError('')
    try {
      await runAgentTask(workspaceId, taskId)
      await loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setRunningTaskId('')
    }
  }

  const handlePauseTask = async (taskId) => {
    if (!workspaceId) return
    setUpdatingTaskId(taskId)
    setError('')
    try {
      await pauseAgentTask(workspaceId, taskId)
      await loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setUpdatingTaskId('')
    }
  }

  const handleResumeTask = async (taskId) => {
    if (!workspaceId) return
    setUpdatingTaskId(taskId)
    setError('')
    try {
      await resumeAgentTask(workspaceId, taskId)
      await loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setUpdatingTaskId('')
    }
  }

  const handleDeleteTask = async (taskId) => {
    if (!workspaceId) return
    if (!window.confirm(t('topicScout.confirmDelete'))) return
    setUpdatingTaskId(taskId)
    setError('')
    try {
      await deleteAgentTask(workspaceId, taskId)
      await loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setUpdatingTaskId('')
    }
  }

  return (
    <AppShell
      title={t('topicScout.title')}
      subtitle={t('topicScout.subtitle')}
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
      <AgentSectionLayout workspaceId={workspaceId} activeItem="topic-scout">
      {error && <p className="error">{error}</p>}
      <div className="card" style={{ marginBottom: '1rem' }}>
        <button
          type="button"
          onClick={() => setIsCreateTaskExpanded((prev) => !prev)}
          style={{
            width: '100%',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: '1rem',
            background: 'transparent',
            border: 'none',
            color: 'inherit',
            padding: 0,
            cursor: 'pointer',
            textAlign: 'left',
          }}
          aria-expanded={isCreateTaskExpanded}
        >
          <div>
            <h2 style={{ margin: 0 }}>{t('topicScout.create.title')}</h2>
            <p className="muted" style={{ margin: '0.4rem 0 0 0' }}>
              {t('topicScout.create.subtitle')}
            </p>
          </div>
          <span className="muted" style={{ whiteSpace: 'nowrap' }}>
            {isCreateTaskExpanded ? t('common.collapse') : t('common.expand')}
          </span>
        </button>
        {isCreateTaskExpanded && !canUseAgentSearch && billingEnabled && (
          <div style={{ display: 'grid', gap: '0.75rem', marginTop: '1.25rem' }}>
            <p className="muted" style={{ margin: 0 }}>
              {t('topicScout.paidOnlyHint')}
            </p>
            <div>
              <Link to={upgradeLink} className="btn btn-secondary btn-small">
                {t('common.upgradePlan')}
              </Link>
            </div>
          </div>
        )}
        {isCreateTaskExpanded && canUseAgentSearch && (
          <div style={{ display: 'grid', gap: '1rem', marginTop: '1.25rem' }}>
            <div
              style={{
                display: 'grid',
                gap: '1rem',
                gridTemplateColumns: 'minmax(220px, 1fr) minmax(320px, 1.4fr) minmax(320px, 1.4fr)',
              }}
            >
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="scout-channel">{t('topicScout.form.channel')}</label>
                <select
                  id="scout-channel"
                  value={channelId}
                  onChange={(e) => setChannelId(e.target.value)}
                  className="form-control"
                >
                  {channels.length === 0 && <option value="">{t('topicScout.form.noChannels')}</option>}
                  {channels.map((channel) => (
                    <option key={channel.id} value={channel.id}>
                      {channel.title || channel.platform_channel_id}
                    </option>
                  ))}
                </select>
                <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                  {t('topicScout.form.channelHint')}
                </p>
              </div>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="scout-goal">{t('topicScout.form.goal')}</label>
                <textarea
                  id="scout-goal"
                  value={goalText}
                  onChange={(e) => setGoalText(e.target.value)}
                  className="form-control post-editor-summary"
                  rows={4}
                  placeholder={t('topicScout.form.goalPlaceholder')}
                />
                <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                  {t('topicScout.form.goalHint')}
                </p>
              </div>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="scout-editorial-instructions">{t('topicScout.form.instructions')}</label>
                <textarea
                  id="scout-editorial-instructions"
                  value={editorialInstructions}
                  onChange={(e) => setEditorialInstructions(e.target.value)}
                  className="form-control post-editor-summary"
                  rows={4}
                  placeholder={t('topicScout.form.instructionsPlaceholder')}
                />
                <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                  {t('topicScout.form.instructionsHint')}
                </p>
              </div>
            </div>

            <div
              style={{
                display: 'grid',
                gap: '1rem',
                gridTemplateColumns: 'minmax(240px, 1.15fr) minmax(180px, 0.8fr) minmax(220px, 0.9fr) minmax(320px, 1.35fr)',
                alignItems: 'start',
              }}
            >
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="scout-schedule-mode">{t('topicScout.form.schedule')}</label>
                <select
                  id="scout-schedule-mode"
                  value={scheduleMode}
                  onChange={(e) => setScheduleMode(e.target.value)}
                  className="form-control"
                >
                  {scheduleModeOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <div style={{ display: 'grid', gap: '0.75rem', marginTop: '0.75rem' }}>
                  {(scheduleMode === 'daily' || scheduleMode === 'weekdays' || scheduleMode === 'weekly') && (
                    <div className="form-group" style={{ marginBottom: 0 }}>
                      <label htmlFor="scout-schedule-time">{t('topicScout.form.startTime')}</label>
                      <input
                        id="scout-schedule-time"
                        type="time"
                        value={scheduleTime}
                        onChange={(e) => setScheduleTime(e.target.value)}
                        className="form-control"
                      />
                    </div>
                  )}
                  {scheduleMode === 'weekly' && (
                    <div className="form-group" style={{ marginBottom: 0 }}>
                      <label htmlFor="scout-schedule-weekday">{t('topicScout.form.weekday')}</label>
                      <select
                        id="scout-schedule-weekday"
                        value={scheduleWeekday}
                        onChange={(e) => setScheduleWeekday(e.target.value)}
                        className="form-control"
                      >
                        {weekdayOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>
                <p className="muted" style={{ margin: '0.5rem 0 0 0' }}>
                  {scheduleDescription}
                  {scheduleCron ? t('topicScout.form.resultCron', { cron: scheduleCron }) : ''}
                </p>
              </div>

              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="scout-max-candidates">{t('topicScout.form.maxCandidates')}</label>
                <select
                  id="scout-max-candidates"
                  value={String(maxCandidatesPerRun)}
                  onChange={(e) => setMaxCandidatesPerRun(Number(e.target.value) || 5)}
                  className="form-control"
                >
                  {MAX_CANDIDATES_OPTIONS.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="scout-autonomy">{t('topicScout.form.autonomy')}</label>
                <select
                  id="scout-autonomy"
                  value={autonomyMode}
                  onChange={(e) => setAutonomyMode(e.target.value)}
                  className="form-control"
                >
                  {autonomyModeOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                  <strong>{selectedAutonomyMode.label}:</strong> {selectedAutonomyMode.hint}
                </p>
              </div>

              <div className="form-group" style={{ marginBottom: 0 }}>
                <label>{t('topicScout.form.searchImageMode')}</label>
                <div className="segmented-control topic-scout-image-mode" role="radiogroup" aria-label={t('topicScout.form.searchImageMode')}>
                  {SEARCH_IMAGE_MODES.map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      className={searchImageMode === mode ? 'active' : ''}
                      role="radio"
                      aria-checked={searchImageMode === mode}
                      onClick={() => setSearchImageMode(mode)}
                    >
                      {t(`topicScout.form.searchImageMode.${mode}`)}
                    </button>
                  ))}
                </div>
                <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                  {t(`topicScout.form.searchImageModeHint.${searchImageMode}`)}
                </p>
              </div>

              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="scout-seeds">{t('topicScout.form.seedUrls')}</label>
                <textarea
                  id="scout-seeds"
                  value={seedUrls}
                  onChange={(e) => setSeedUrls(e.target.value)}
                  className="form-control post-editor-summary"
                  rows={3}
                  placeholder={t('topicScout.form.seedUrlsPlaceholder')}
                />
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
              <p className="muted" style={{ margin: 0 }}>
                {t('topicScout.form.bridgesHint')}
              </p>
              <button
                type="button"
                className="btn"
                onClick={handleCreateTask}
                disabled={saving || !channelId || !goalText.trim()}
              >
                {saving ? t('common.savingShort') : t('topicScout.form.create')}
              </button>
            </div>
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(360px, 1fr) minmax(320px, 0.9fr)', gap: '1rem' }}>
        <div style={{ display: 'grid', gap: '1rem' }}>
          <div className="card" style={{ minHeight: '30rem' }}>
            <h2 style={{ marginTop: 0 }}>{t('topicScout.tasks.title')}</h2>
            {loading && <p className="muted">{t('common.loading')}</p>}
            {!loading && tasks.length === 0 && <p className="muted">{t('topicScout.tasks.empty')}</p>}
            {!loading && tasks.length > 0 && (
              <div style={{ display: 'grid', gap: '0.75rem' }}>
                {tasks.map((task) => (
                  <div
                    key={task.id}
                    style={{
                      border: task.id === selectedTaskId ? '1px solid var(--primary)' : '1px solid var(--border)',
                      borderRadius: '12px',
                      padding: '0.85rem',
                      background: task.id === selectedTaskId ? 'var(--surface-strong)' : 'transparent',
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedTaskId(task.id)}
                      style={{
                        width: '100%',
                        background: 'transparent',
                        border: 'none',
                        padding: 0,
                        color: 'inherit',
                        textAlign: 'left',
                        cursor: 'pointer',
                      }}
                    >
                      <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem' }}>
                          <strong>{task.goal_text}</strong>
                          <span className="muted" style={{ fontSize: '0.85rem' }}>
                            {taskStatusLabel(task.status, t)}
                          </span>
                        </div>
                        {task.editorial_instructions ? (
                          <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
                            {t('topicScout.task.instructions', { instructions: task.editorial_instructions })}
                          </p>
                        ) : null}
                        <p className="muted" style={{ margin: '0.3rem 0 0 0' }}>
                          {humanizeCron(task.schedule_cron, weekdayOptions, t)} · {autonomyModeLabel(task.autonomy_mode, t)} · {t(`topicScout.imageMode.short.${task.task_config?.search_image_mode || 'none'}`)}
                        </p>
                      </div>
                    </button>
                    <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.85rem' }}>
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        onClick={() => handleRunTask(task.id)}
                        disabled={!canUseAgentSearch || runningTaskId === task.id || updatingTaskId === task.id}
                      >
                        {runningTaskId === task.id ? t('topicScout.actions.running') : t('topicScout.actions.runNow')}
                      </button>
                      {!canUseAgentSearch && billingEnabled && (
                        <Link to={upgradeLink} className="btn btn-secondary btn-small">
                          {t('common.upgrade')}
                        </Link>
                      )}
                      {task.status === 'active' ? (
                        <button
                          type="button"
                          className="btn btn-secondary btn-small"
                          onClick={() => handlePauseTask(task.id)}
                          disabled={updatingTaskId === task.id}
                        >
                          {updatingTaskId === task.id ? t('common.savingShort') : t('common.pause')}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-secondary btn-small"
                          onClick={() => handleResumeTask(task.id)}
                          disabled={updatingTaskId === task.id}
                        >
                          {updatingTaskId === task.id ? t('common.savingShort') : t('common.resume')}
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        onClick={() => handleDeleteTask(task.id)}
                        disabled={updatingTaskId === task.id}
                      >
                        {updatingTaskId === task.id ? t('common.savingShort') : t('common.delete')}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: 'grid', gap: '1rem' }}>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>{t('topicScout.selected.title')}</h3>
            {!selectedTask && <p className="muted">{t('topicScout.selected.empty')}</p>}
            {selectedTask && (
              <div style={{ display: 'grid', gap: '0.75rem' }}>
                <div>
                  <div className="muted">{t('common.topic')}</div>
                  <strong>{selectedTask.goal_text}</strong>
                </div>
                {selectedTask.editorial_instructions ? (
                  <div>
                    <div className="muted">{t('common.instructions')}</div>
                    <div>{selectedTask.editorial_instructions}</div>
                  </div>
                ) : null}
                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                  <div>
                    <div className="muted">{t('topicScout.form.schedule')}</div>
                    <div>{humanizeCron(selectedTask.schedule_cron, weekdayOptions, t)}</div>
                  </div>
                  <div>
                    <div className="muted">{t('common.mode')}</div>
                    <div>{autonomyModeLabel(selectedTask.autonomy_mode, t)}</div>
                  </div>
                  <div>
                    <div className="muted">{t('common.status')}</div>
                    <div>{taskStatusLabel(selectedTask.status, t)}</div>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>
              {selectedTask ? t('topicScout.runs.titleWithTask', { task: selectedTask.goal_text }) : t('topicScout.runs.title')}
            </h3>
            {!selectedTask && <p className="muted">{t('topicScout.runs.noTask')}</p>}
            {selectedTask && latestRuns.length === 0 && <p className="muted">{t('topicScout.runs.empty')}</p>}
            {latestRuns.length > 0 && (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {latestRuns.map((run) => (
                  <li
                    key={run.id}
                    style={{
                      padding: '0.9rem 0',
                      borderBottom: '1px solid var(--border)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
                      <div>
                        <strong>{statusLabels[run.status] || run.status}</strong>
                        <p className="muted" style={{ margin: '0.3rem 0 0 0' }}>
                          {run.user_request || run.topic_definition || run.id}
                        </p>
                      </div>
                      <div style={{ textAlign: 'right' }}>
                        <div className="muted">{formatDate(run.created_at, locale)}</div>
                        <Link to={`/workspaces/${workspaceId}/agents/runs/${run.id}`} className="btn btn-secondary btn-small">
                          {t('topicScout.runs.open')}
                        </Link>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="card" id="results-table" ref={resultsRef} tabIndex={-1}>
            <h3 style={{ marginTop: 0 }}>
              {selectedTask ? t('topicScout.candidates.titleWithTask', { task: selectedTask.goal_text }) : t('topicScout.candidates.title')}
            </h3>
            {!selectedTask && <p className="muted">{t('topicScout.candidates.noTask')}</p>}
            {selectedTask && latestReviewItems.length === 0 && <p className="muted">{t('topicScout.candidates.empty')}</p>}
            {latestReviewItems.length > 0 && (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {latestReviewItems.map((item) => (
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
                        <div className="muted">{statusLabels[item.status] || item.status}</div>
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
      </div>
      </AgentSectionLayout>
    </AppShell>
  )
}
