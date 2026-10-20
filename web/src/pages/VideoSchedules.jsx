import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import AgentSectionLayout from '../components/AgentSectionLayout'
import { useAuth } from '../useAuth'
import { useI18n } from '../i18n'
import { clearToken } from '../adapters/sessionToken'
import { isSelfhostMode } from '../adapters/runtime'
import { listChannelRegistry } from '../adapters/channels'
import { createAgentTask, deleteAgentTask, listAgentRuns, listAgentTasks, pauseAgentTask, resumeAgentTask, runAgentTask } from '../adapters/agent'
import { getVideo, getVideoCapabilities, uploadVideoMaterial } from '../adapters/video'
import { getAllTimezoneSelectOptions } from '../timezoneOptions'
import './VideoStudio.css'
import VideoMaterialPicker from '../components/VideoMaterialPicker'

import { videoSceneInput as sceneInput } from '../videoSceneInput'
const activeRun = (run) => ['queued', 'running'].includes(run.status)

export default function VideoSchedules() {
  const { workspaceId } = useParams()
  const [query] = useSearchParams()
  const sourceId = query.get('source')
  const sourceVersion = Number(query.get('version'))
  const selectedId = query.get('task')
  const navigate = useNavigate()
  const { user } = useAuth()
  const { t, locale } = useI18n()
  const [caps, setCaps] = useState(null)
  const [channels, setChannels] = useState([])
  const [tasks, setTasks] = useState([])
  const [runs, setRuns] = useState([])
  const [channelId, setChannelId] = useState('')
  const [brief, setBrief] = useState('')
  const [materials, setMaterials] = useState([])
  const [frequency, setFrequency] = useState('weekly')
  const [weekday, setWeekday] = useState('1')
  const [time, setTime] = useState('09:00')
  const [timezone, setTimezone] = useState(() => user?.profile_timezone || user?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const [manual, setManual] = useState(false)
  const [duration, setDuration] = useState(20)
  const [style, setStyle] = useState('')
  const [sourceSettings, setSourceSettings] = useState({})
  const [narration, setNarration] = useState(true)
  const [voice, setVoice] = useState('Kore')
  const [voiceStyle, setVoiceStyle] = useState('neutral')
  const [language, setLanguage] = useState(locale === 'en' ? 'en' : 'ru')
  const [monthlyBudget, setMonthlyBudget] = useState('')
  const [runBudget, setRunBudget] = useState('')
  const [notify, setNotify] = useState(true)
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const createKey = useRef(null)
  const runKeys = useRef({})
  const context = useRef(workspaceId)
  context.current = workspaceId

  const refresh = async () => {
    const [taskResponse, runResponse] = await Promise.all([listAgentTasks(workspaceId), listAgentRuns(workspaceId)])
    if (context.current !== workspaceId) return
    setTasks((taskResponse.items || []).filter((item) => item.mode === 'video_autopilot'))
    setRuns((runResponse.items || []).filter((item) => item.graph_name === 'video_autopilot'))
  }
  useEffect(() => {
    let current = true
    setLoading(true)
    setError('')
    setTasks([]); setRuns([]); setBrief(''); setMaterials([])
    setManual(false); setDuration(20); setStyle(''); setSourceSettings({}); setMonthlyBudget(''); setRunBudget(''); setNotify(true)
    setNarration(true); setVoice('Kore'); setVoiceStyle('neutral'); setLanguage(locale === 'en' ? 'en' : 'ru')
    createKey.current = null; runKeys.current = {}
    Promise.all([getVideoCapabilities(workspaceId), listChannelRegistry(workspaceId), listAgentTasks(workspaceId),
      listAgentRuns(workspaceId), sourceId ? getVideo(workspaceId, sourceId) : Promise.resolve(null)])
      .then(([capabilities, registry, taskResponse, runResponse, source]) => {
        if (!current) return
        const editorial = (registry.items || []).filter((item) => item.platform === 'postbridge')
        setCaps(capabilities); setChannels(editorial); setChannelId(editorial[0]?.id || '')
        const rows = (taskResponse.items || []).filter((item) => item.mode === 'video_autopilot')
        setTasks(rows); setRuns((runResponse.items || []).filter((item) => item.graph_name === 'video_autopilot'))
        setExpanded(!!source || rows.length === 0)
        if (source) {
          const selected = source.revisions?.find((revision) => revision.version === sourceVersion) || source.current
          setBrief(source.brief); setMaterials(selected.scenes.map((scene) => ({ ...scene })))
          setSourceSettings(Object.fromEntries(['template', 'brand_name', 'brand_color'].filter((key) => selected[key] != null).map((key) => [key, selected[key]])))
          setLanguage(selected.language || 'ru'); setVoice(selected.voice || 'Kore'); setVoiceStyle(selected.voice_style || 'neutral')
          setManual(selected.scenes.some((scene) => scene.speech_asset_id))
        }
      }).catch((e) => { if (current) setError(e.message) }).finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [workspaceId, sourceId, sourceVersion])
  const producing = runs.some(activeRun)
  useEffect(() => {
    if (!producing) return
    let inFlight = false
    const timer = setInterval(async () => {
      if (inFlight) return
      inFlight = true
      try { await refresh() } catch { /* Keep existing results during a temporary outage. */ }
      finally { inFlight = false }
    }, 5000)
    return () => clearInterval(timer)
  }, [workspaceId, producing])

  const perform = async (action) => {
    if (busy) return
    setBusy(true); setError('')
    try { await action() } catch (e) { if (context.current === workspaceId) setError(e.message) }
    finally { if (context.current === workspaceId) setBusy(false) }
  }
  const upload = (event) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    perform(async () => {
      if (materials.length + files.length > 8) throw new Error(t('video.tooMany'))
      if (files.some((file) => file.size > caps.max_upload_bytes)) throw new Error(t('video.tooLarge'))
      for (const file of files) {
        const material = await uploadVideoMaterial(workspaceId, file)
        if (context.current !== workspaceId) return
        setMaterials((previous) => [...previous, { asset_id: material.media_asset_id, name: file.name,
          caption: '', duration_seconds: 5 }])
      }
    })
  }
  const submit = (event) => {
    event.preventDefault()
    perform(async () => {
      const [hour, minute] = time.split(':').map(Number)
      const cron = `${minute} ${hour} * * ${frequency === 'daily' ? '*' : frequency === 'weekdays' ? '1-5' : weekday}`
      const template = { brief: brief.trim(), mode: caps.autopilot_enabled && !manual ? 'autopilot' : 'manual',
        language, duration_seconds: Number(duration), style, ...sourceSettings,
        ...(caps.speech_enabled && narration && !manual ? { narration_enabled: true, voice, voice_style: voiceStyle } : {}),
        ...(materials.some((scene) => scene.speech_asset_id) ? { voice, voice_style: voiceStyle } : {}),
        scenes: materials.map(sceneInput) }
      const fingerprint = JSON.stringify({ template, cron, timezone, channelId, monthlyBudget, runBudget, notify })
      if (createKey.current?.fingerprint !== fingerprint) createKey.current = { fingerprint, key: crypto.randomUUID() }
      const key = createKey.current.key
      await createAgentTask(workspaceId, { channel_id: channelId, mode: 'video_autopilot', goal_text: template.brief,
        schedule_cron: cron, timezone, max_candidates_per_run: 1, autonomy_mode: 'draft_approval',
        ...(isSelfhostMode() ? { created_by: user?.id || 'selfhost' } : {}),
        task_config: { request_key: key, video: { ...template, request_key: key }, notify: !isSelfhostMode() && notify,
          max_units_per_run: template.mode === 'autopilot' && runBudget ? Number(runBudget) : null,
          max_units_per_month: template.mode === 'autopilot' && monthlyBudget ? Number(monthlyBudget) : null } })
      if (context.current !== workspaceId) return
      createKey.current = null; setExpanded(false)
      await refresh()
    })
  }
  const runNow = (task) => perform(async () => {
    const key = runKeys.current[task.id] || crypto.randomUUID()
    runKeys.current[task.id] = key
    await runAgentTask(workspaceId, task.id, { requestKey: key })
    delete runKeys.current[task.id]
    await refresh()
  })
  const date = (value) => value ? new Date(value).toLocaleString(locale === 'en' ? 'en-US' : 'ru-RU') : '—'
  const status = (run) => run.error_code === 'VIDEO_SCHEDULE_OVERLAP' ? t('videoSchedule.skipped')
    : t(`videoSchedule.run.${run.status}`, { defaultValue: run.status })
  const shownTasks = tasks.filter((task) => !selectedId || task.id === selectedId)
  if (selectedId && !shownTasks.length) {
    const previous = runs.find((run) => run.agent_task_id === selectedId)
    if (previous) shownTasks.push({ id: selectedId, goal_text: previous.user_request || t('videoSchedule.title'),
      status: 'archived', timezone: previous.video_execution?.input_snapshot?.timezone || '' })
  }

  return <AppShell title={t('videoSchedule.title')} subtitle={t('videoSchedule.subtitle')} user={user}
    onLogout={() => { clearToken(); navigate('/') }}>
    <AgentSectionLayout workspaceId={workspaceId} activeItem="videos">
      {error && <p className="error" role="alert">{error}</p>}
      {loading ? <p>{t('common.loading')}</p> : <div className="video-studio">
        {caps?.enabled && caps.supports_schedules && <details className="card" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
          <summary>{t('videoSchedule.new')}</summary>
          <form onSubmit={submit} className="video-create">
            <label>{t('video.brief')}<textarea required maxLength={4000} value={brief} onChange={(e) => setBrief(e.target.value)} rows={3} /></label>
            <label>{t('video.addMaterials')}<input type="file" multiple accept="image/png,image/jpeg,image/webp,video/mp4,video/webm" onChange={upload} disabled={busy} /></label>
            <VideoMaterialPicker workspaceId={workspaceId} disabled={busy || materials.length >= 8} onChoose={(item) => setMaterials((old) => old.some((s) => s.asset_id === item.asset_id) ? old : [...old, item])} />
            <p className="muted">{t(caps.autopilot_enabled && !manual ? 'video.autoMaterialHint' : 'video.materialHint')}</p>
            {materials.map((item, index) => <div className="inline-actions" key={`${index}-${item.asset_id}`}><span>{item.name || t('video.scene', { number: index + 1 })}</span><button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => setMaterials((old) => old.filter((_, i) => i !== index))}>{t('common.delete')}</button></div>)}
            <label>{t('videoSchedule.frequency')}<select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
              <option value="daily">{t('topicScout.schedule.daily')}</option><option value="weekdays">{t('topicScout.schedule.weekdays')}</option><option value="weekly">{t('topicScout.schedule.weekly')}</option>
            </select></label>
            {frequency === 'weekly' && <label>{t('videoSchedule.weekday')}<select value={weekday} onChange={(e) => setWeekday(e.target.value)}>{['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'].map((day, index) => <option key={day} value={String((index + 1) % 7)}>{t(`weekday.${day}`)}</option>)}</select></label>}
            <label>{t('videoSchedule.time')}<input type="time" required value={time} onChange={(e) => setTime(e.target.value)} /></label>
            <label>{t('videoSchedule.timezone')}<select aria-label={t('videoSchedule.timezone')} value={timezone} onChange={(e) => setTimezone(e.target.value)}>{getAllTimezoneSelectOptions(t, timezone).map((zone) => <option key={zone.value} value={zone.value}>{zone.label}</option>)}</select></label>
            <p className="muted">{t('videoSchedule.draftOnly')}</p>
            <details><summary>{t('video.options')}</summary>
              {!materials.some((scene) => scene.speech_asset_id) && <label>{t('videoVoice.language')}<select value={language} onChange={(e) => setLanguage(e.target.value)}>{['ru', 'en'].map((value) => <option key={value} value={value}>{t(`videoVoice.language.${value}`)}</option>)}</select></label>}
              {channels.length > 1 && <label>{t('videoSchedule.context')}<select value={channelId} onChange={(e) => setChannelId(e.target.value)}>{channels.map((channel) => <option value={channel.id} key={channel.id}>{channel.title}</option>)}</select></label>}
              {caps.autopilot_enabled && !materials.some((scene) => scene.speech_asset_id) && <label><input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} />{t('video.manualOnly')}</label>}
              {caps.autopilot_enabled && !manual && <>
                {caps.speech_enabled && <>
                  <label><input type="checkbox" checked={narration} onChange={(e) => setNarration(e.target.checked)} />{t('videoVoice.enabled')}</label>
                  {narration && <label>{t('videoVoice.voice')}<select value={voice} onChange={(e) => setVoice(e.target.value)}>{caps.speech_voices.map((value) => <option key={value} value={value}>{t(`videoVoice.voice.${value}`)}</option>)}</select></label>}
                </>}
                <label>{t('video.targetDuration')}<select value={duration} onChange={(e) => setDuration(e.target.value)}>{[10, 15, 20, 30, 45].map((seconds) => <option key={seconds} value={seconds}>{t('video.seconds', { seconds })}</option>)}</select></label>
                <label>{t('video.style')}<input maxLength={500} value={style} onChange={(e) => setStyle(e.target.value)} /></label>
                <label>{t('videoSchedule.runBudget')}<input type="number" min="1" max="100000000" value={runBudget} onChange={(e) => setRunBudget(e.target.value)} placeholder={t('videoSchedule.planBudget')} /></label>
                <label>{t('videoSchedule.monthlyBudget')}<input type="number" min="1" max="100000000" value={monthlyBudget} onChange={(e) => setMonthlyBudget(e.target.value)} placeholder={t('videoSchedule.planBudget')} /></label>
                <p className="muted">{t('videoSchedule.budgetHint')}</p>
              </>}
              {!isSelfhostMode() && <label><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />{t('videoSchedule.notify')}</label>}
            </details>
            {!channelId && <p className="muted">{t('topicScout.errors.noChannel')}</p>}
            <button className="btn" disabled={busy || !brief.trim() || !channelId || (!(caps.autopilot_enabled && !manual) && !materials.length)}>{t('videoSchedule.save')}</button>
          </form>
        </details>}
        {!caps?.supports_schedules && <p>{t('video.unavailable')}</p>}
        {!tasks.length && <p className="muted">{t('videoSchedule.empty')}</p>}
        {shownTasks.map((task) => <section className="card" key={task.id}>
          <h2>{task.goal_text}</h2><p>{t(`topicScout.taskStatus.${task.status}`)} · {task.timezone}</p>
          <p className="muted">{t('videoSchedule.nextRun', { date: date(task.next_run_at) })}</p>
          {task.status !== 'archived' && <div className="inline-actions">
            <button className="btn" disabled={busy || !caps?.enabled || runs.some((run) => run.agent_task_id === task.id && activeRun(run))} onClick={() => runNow(task)}>{t('videoSchedule.runNow')}</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => perform(async () => { await (task.status === 'paused' ? resumeAgentTask : pauseAgentTask)(workspaceId, task.id); await refresh() })}>{t(task.status === 'paused' ? 'videoSchedule.resume' : 'videoSchedule.pause')}</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => perform(async () => { await deleteAgentTask(workspaceId, task.id); await refresh() })}>{t('videoSchedule.archive')}</button>
          </div>}
          <p className="muted">{t('videoSchedule.pauseHint')}</p>
          <ul>{runs.filter((run) => run.agent_task_id === task.id).slice(0, 10).map((run) => <li key={run.id}>
            {date(run.created_at)} · {status(run)}{' '}
            {run.error_code && run.error_code !== 'VIDEO_SCHEDULE_OVERLAP' && <span>{t(`api.error.${run.error_code}`, { defaultValue: t('videoSchedule.run.failed') })}{' '}</span>}
            {run.video_execution?.project_id && <Link to={`/workspaces/${workspaceId}/content/videos/${run.video_execution.project_id}`}>{t('video.open')}</Link>}{' '}
            <Link to={`/workspaces/${workspaceId}/agents/runs/${run.id}`}>{t('videoSchedule.runDetails')}</Link>
          </li>)}</ul>
        </section>)}
        <Link to={`/workspaces/${workspaceId}/content/videos`}>{t('videoSchedule.openStudio')}</Link>
      </div>}
    </AgentSectionLayout>
  </AppShell>
}
