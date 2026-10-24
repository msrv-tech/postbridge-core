import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import AgentSectionLayout from '../components/AgentSectionLayout'
import { useAuth } from '../useAuth'
import { useI18n } from '../i18n'
import { clearToken } from '../adapters/sessionToken'
import { isSelfhostMode } from '../adapters/runtime'
import { listVideoSchedules } from '../adapters/video'
import { listAgentRuns, pauseAgentTask } from '../adapters/agent'
import VideoSchedulesManual from './VideoSchedulesManual'
import './VideoStudio.css'

export default function VideoSchedules() {
  return isSelfhostMode() ? <VideoSchedulesManual /> : <HostedVideoSchedules />
}

function HostedVideoSchedules() {
  const { workspaceId } = useParams()
  const [query] = useSearchParams()
  const { user } = useAuth()
  const { t, locale } = useI18n()
  const navigate = useNavigate()
  const [tasks, setTasks] = useState([]), [runs, setRuns] = useState([])
  const [question, setQuestion] = useState(''), [editing, setEditing] = useState(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const field = useRef(null)
  const context = useRef(workspaceId)
  context.current = workspaceId
  const sourceId = query.get('source'), sourceVersion = Number(query.get('version'))
  const selectedId = query.get('task')
  useEffect(() => {
    let current = true, inFlight = false
    setLoading(true); setError(''); setTasks([]); setRuns([]); setEditing(null); setQuestion('')
    const refresh = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const [schedules, history] = await Promise.all([listVideoSchedules(workspaceId), listAgentRuns(workspaceId)])
        if (current) { setTasks(schedules.items || []); setRuns((history.items || []).filter((r) => r.graph_name === 'video_autopilot')) }
      } catch (e) { if (current) setError(e.message) }
      finally { inFlight = false; if (current) setLoading(false) }
    }
    const afterApply = (event) => { if (event.detail?.workspaceId === workspaceId) void refresh() }
    void refresh()
    const timer = setInterval(refresh, 10000)
    window.addEventListener('postbridge-video-schedules-updated', afterApply)
    return () => { current = false; clearInterval(timer); window.removeEventListener('postbridge-video-schedules-updated', afterApply) }
  }, [workspaceId])
  useEffect(() => { setEditing(null); setQuestion('') }, [workspaceId, selectedId, sourceId, sourceVersion])
  useEffect(() => {
    const context = (event) => {
      if (event.detail?.workspaceId !== workspaceId || !sourceId) return
      event.detail.projectId = sourceId
      if (sourceVersion > 0) event.detail.selectedVersion = sourceVersion
    }
    window.addEventListener('postbridge-video-draft-context', context)
    return () => window.removeEventListener('postbridge-video-draft-context', context)
  }, [workspaceId, sourceId, sourceVersion])
  const ask = (event) => {
    event.preventDefault()
    if (!question.trim()) return
    const text = editing ? t('videoSchedule.agentEditRequest', { title: editing.goal_text, request: question.trim() }) : question.trim()
    window.dispatchEvent(new CustomEvent('postbridge-assistant-ask', { detail: { workspaceId, question: text } }))
  }
  const pause = async (task) => {
    setBusy(true); setError('')
    try {
      await pauseAgentTask(workspaceId, task.id)
      if (context.current !== workspaceId) return
      setTasks((old) => old.map((item) => item.id === task.id ? { ...item, status: 'paused', next_run_at: null } : item))
    } catch (e) { if (context.current === workspaceId) setError(e.message) }
    finally { if (context.current === workspaceId) setBusy(false) }
  }
  const date = (value) => value ? new Date(value).toLocaleString(locale === 'en' ? 'en-US' : 'ru-RU') : '—'
  return <AppShell title={t('videoSchedule.title')} subtitle={t('videoSchedule.subtitle')} user={user}
    onLogout={() => { clearToken(); navigate('/') }}>
    <AgentSectionLayout workspaceId={workspaceId} activeItem="videos">
      <div className="video-studio">
        {error && <p className="error" role="alert">{error}</p>}
        <section className="card">
          <h2>{t(editing ? 'videoSchedule.agentEdit' : 'videoSchedule.agentTitle')}</h2>
          {editing && <p>{editing.goal_text}</p>}
          <p className="muted">{t('videoSchedule.agentHint')}</p>
          <form className="video-create" onSubmit={ask}>
            <label>{t('videoSchedule.agentRequest')}<textarea ref={field} rows={3} maxLength={4000} required
              value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={t('videoSchedule.agentExample')} /></label>
            <div className="inline-actions"><button className="btn" disabled={!question.trim()}>{t('video.askAssistant')}</button>
              {editing && <button type="button" className="btn btn-secondary" onClick={() => { setEditing(null); setQuestion('') }}>{t('common.cancel')}</button>}</div>
          </form>
        </section>
        {loading ? <p>{t('common.loading')}</p> : !tasks.length && <p className="muted">{t('videoSchedule.empty')}</p>}
        {tasks.filter((task) => !selectedId || task.id === selectedId).map((task) => <section className="card" key={task.id}>
          <h2>{task.goal_text}</h2>
          <p>{t(`topicScout.taskStatus.${task.status}`)} · {task.timezone}</p>
          <p className="muted">{t('videoSchedule.nextRun', { date: date(task.next_run_at) })}</p>
          {task.task_config?.auto_publication && <p>{t('videoAuto.activePolicy')}</p>}
          {task.status !== 'archived' && <div className="inline-actions">
            <button className="btn btn-secondary" onClick={() => { setEditing(task); setQuestion(''); field.current?.focus(); field.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }}>{t('videoSchedule.agentEdit')}</button>
            {task.status === 'active' && <button className="btn btn-secondary" disabled={busy} onClick={() => pause(task)}>{t('videoSchedule.pause')}</button>}
          </div>}
          <ul>{runs.filter((run) => run.agent_task_id === task.id).slice(0, 10).map((run) => <li key={run.id}>
            {date(run.created_at)} · {t(`videoSchedule.run.${run.status}`, { defaultValue: run.status })}{' '}
            {run.video_execution?.auto_publication?.status && <span>{t(`videoAuto.status.${run.video_execution.auto_publication.status}`)}{' '}</span>}
            {run.error_code && <span>{t(`api.error.${run.error_code}`, { defaultValue: t('videoSchedule.run.failed') })}{' '}</span>}
            {run.video_execution?.project_id && <Link to={`/workspaces/${workspaceId}/content/videos/${run.video_execution.project_id}`}>{t('video.open')}</Link>}{' '}
            <Link to={`/workspaces/${workspaceId}/agents/runs/${run.id}`}>{t('videoSchedule.runDetails')}</Link>
          </li>)}</ul>
        </section>)}
        <Link to={`/workspaces/${workspaceId}/content/videos`}>{t('videoSchedule.openStudio')}</Link>
      </div>
    </AgentSectionLayout>
  </AppShell>
}
