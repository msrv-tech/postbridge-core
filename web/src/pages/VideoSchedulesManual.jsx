import VoicePreview from '../components/VoicePreview'
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
import { getVideo, getVideoCapabilities, uploadVideoMaterial, getVideoScheduleBridges, listVideoMaterials, listVideoSchedules, updateVideoSchedule } from '../adapters/video'
import { getAllTimezoneSelectOptions } from '../timezoneOptions'
import './VideoStudio.css'
import VideoMaterialPicker from '../components/VideoMaterialPicker'
import VideoVariantChoices from '../components/VideoVariantChoices'

import { videoSceneInput as sceneInput } from '../videoSceneInput'
const activeRun = (run) => ['queued', 'running'].includes(run.status)

export default function VideoSchedulesManual() {
  const { workspaceId } = useParams()
  const [query] = useSearchParams()
  const sourceId = query.get('source')
  const sourceVersion = Number(query.get('version'))
  const selectedId = query.get('task')
  const navigate = useNavigate()
  const { user } = useAuth()
  const { t, locale } = useI18n()
  const [editing, setEditing] = useState(null)
  const [folderMode, setFolderMode] = useState(false), [folder, setFolder] = useState(''), [folders, setFolders] = useState([])
  const [materialLimit, setMaterialLimit] = useState(6), [customCron, setCustomCron] = useState('')
  const [splitLanguages, setSplitLanguages] = useState(false), [languageBridges, setLanguageBridges] = useState({})
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
  const [generatedVisuals, setGeneratedVisuals] = useState('images')
  const [sourceSettings, setSourceSettings] = useState({})
  const [narration, setNarration] = useState(true)
  const [voice, setVoice] = useState('Kore')
  const [voiceStyle, setVoiceStyle] = useState('neutral')
  const [language, setLanguage] = useState(locale === 'en' ? 'en' : 'ru')
  const [notify, setNotify] = useState(true)
  const [count, setCount] = useState(1)
  const [extraVoices, setExtraVoices] = useState(false)
  const [variants, setVariants] = useState([])
  const [autoPublish, setAutoPublish] = useState(false), [autoBridges, setAutoBridges] = useState([]), [autoSelected, setAutoSelected] = useState([])
  const [autoCaption, setAutoCaption] = useState(''), [autoConfirmed, setAutoConfirmed] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const createKey = useRef(null)
  const runKeys = useRef({})
  const context = useRef(workspaceId)
  context.current = workspaceId

  useEffect(() => {
    let current = true
    setAutoBridges([]); setAutoConfirmed(false); setError('')
    if (autoPublish && channelId) getVideoScheduleBridges(workspaceId, channelId)
      .then((value) => { if (current) setAutoBridges(value.items || []) })
      .catch((e) => { if (current) setError(e.message) })
    return () => { current = false }
  }, [workspaceId, channelId, autoPublish])

  const refresh = async () => {
    const [taskResponse, runResponse] = await Promise.all([listVideoSchedules(workspaceId), listAgentRuns(workspaceId)])
    if (context.current !== workspaceId) return
    setTasks((taskResponse.items || []).filter((item) => item.mode === 'video_autopilot'))
    setRuns((runResponse.items || []).filter((item) => item.graph_name === 'video_autopilot'))
  }
  useEffect(() => {
    let current = true
    setLoading(true)
    setError('')
    setTasks([]); setRuns([]); setBrief(''); setMaterials([])
    setManual(false); setDuration(20); setStyle(''); setSourceSettings({}); setNotify(true)
    setNarration(true); setVoice('Kore'); setVoiceStyle('neutral'); setLanguage(locale === 'en' ? 'en' : 'ru')
    setCount(1); setExtraVoices(false); setVariants([])
    setAutoPublish(false); setAutoSelected([]); setAutoCaption(''); setAutoConfirmed(false)
    setGeneratedVisuals('images'); setEditing(null); setFolderMode(false); setSplitLanguages(false); setLanguageBridges({})
    listVideoMaterials(workspaceId).then((data) => { if (current) setFolders(data.folders || []) }).catch(() => {})
    createKey.current = null; runKeys.current = {}
    Promise.all([getVideoCapabilities(workspaceId), listChannelRegistry(workspaceId), listVideoSchedules(workspaceId),
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
          setGeneratedVisuals(selected.generated_visuals || 'images')
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
      const cron = frequency === 'custom' ? customCron.trim() : `${minute} ${hour} * * ${frequency === 'daily' ? '*' : frequency === 'weekdays' ? '1-5' : weekday}`
      const template = { brief: brief.trim(), mode: caps.autopilot_enabled && !manual ? 'autopilot' : 'manual',
        language, duration_seconds: Number(duration), style, ...sourceSettings,
        ...(!manual && generatedVisuals === 'video' ? { generated_visuals: 'video' } : {}),
        ...(caps.speech_enabled && narration && !manual ? { narration_enabled: true, voice, voice_style: voiceStyle } : {}),
        ...(materials.some((scene) => scene.speech_asset_id) ? { voice, voice_style: voiceStyle } : {}),
        scenes: folderMode ? [] : materials.map(sceneInput) }
      const batchOptions = { count: template.mode === 'autopilot' ? Number(count) : 1, variants: extraVoices ? variants : [] }
      const autoPolicy = autoPublish ? { confirmed: autoConfirmed, bridge_ids: autoSelected,
        expected_bridges: Object.fromEntries(autoBridges.filter((b) => autoSelected.includes(b.id)).map((b) => [b.id, b.settings_hash])), caption: autoCaption.trim(), ...(splitLanguages ? { language_bridge_ids: Object.fromEntries(languages.map((lang) => [lang, (languageBridges[lang] || []).filter((id) => autoSelected.includes(id))])) } : {}) } : null
      const fingerprint = JSON.stringify({ template, cron, timezone, channelId, notify, batchOptions, autoPolicy, folderMode, folder, materialLimit })
      if (createKey.current?.fingerprint !== fingerprint) createKey.current = { fingerprint, key: crypto.randomUUID() }
      const key = createKey.current.key
      const taskConfig = { auto_publication: autoPolicy, request_key: key, video: { ...template, request_key: key },
        notify: !isSelfhostMode() && notify, ...batchOptions,
        material_folder: folderMode ? folder : null, material_limit: Number(materialLimit),
        max_units_per_run: editing?.task_config?.max_units_per_run ?? null,
        max_units_per_month: editing?.task_config?.max_units_per_month ?? null }
      if (editing) {
        await updateVideoSchedule(workspaceId, editing.id, { expected_hash: editing.template_hash,
          goal_text: template.brief, schedule_cron: cron, timezone, task_config: taskConfig,
          ...(isSelfhostMode() ? { created_by: user?.id || 'selfhost' } : {}) })
      } else {
        await createAgentTask(workspaceId, { channel_id: channelId, mode: 'video_autopilot', goal_text: template.brief,
          schedule_cron: cron, timezone, max_candidates_per_run: 1, autonomy_mode: 'draft_approval',
          ...(isSelfhostMode() ? { created_by: user?.id || 'selfhost' } : {}), task_config: taskConfig })
      }
      if (context.current !== workspaceId) return
      createKey.current = null; setExpanded(false); setEditing(null)
      await refresh()
    })
  }
  const editTask = (task) => {
    const config = task.task_config, video = config.video
    setEditing(task); setExpanded(true); setChannelId(task.channel_id); setBrief(video.brief)
    setMaterials(video.scenes || []); setManual(video.mode === 'manual'); setLanguage(video.language)
    setDuration(video.duration_seconds); setStyle(video.style || ''); setGeneratedVisuals(video.generated_visuals || 'images')
    setNarration(!!video.narration_enabled); setVoice(video.voice || 'Kore'); setVoiceStyle(video.voice_style || 'neutral')
    setSourceSettings(Object.fromEntries(['template', 'brand_name', 'brand_color'].filter((key) => video[key] != null).map((key) => [key, video[key]])))
    setCount(config.count || 1); setVariants(config.variants || []); setExtraVoices(!!config.variants?.length)
    setNotify(config.notify)
    setFolderMode(config.material_folder != null); setFolder(config.material_folder || ''); setMaterialLimit(config.material_limit || 6)
    setTimezone(task.timezone); setCustomCron(task.schedule_cron || '')
    const parts = (task.schedule_cron || '').split(' ')
    const simple = parts.length === 5 && /^\d+$/.test(parts[0]) && /^\d+$/.test(parts[1]) && parts[2] === '*' && parts[3] === '*'
    if (simple && ['*', '1-5', '0', '1', '2', '3', '4', '5', '6'].includes(parts[4])) {
      setTime(`${parts[1].padStart(2, '0')}:${parts[0].padStart(2, '0')}`)
      setFrequency(parts[4] === '*' ? 'daily' : parts[4] === '1-5' ? 'weekdays' : 'weekly'); setWeekday(/^\d$/.test(parts[4]) ? parts[4] : '1')
    } else setFrequency('custom')
    const policy = config.auto_publication
    setAutoPublish(!!policy); setAutoSelected(policy?.bridge_ids || []); setAutoCaption(policy?.caption || ''); setAutoConfirmed(false)
    setSplitLanguages(!!Object.keys(policy?.language_bridge_ids || {}).length); setLanguageBridges(policy?.language_bridge_ids || {})
    createKey.current = null
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  const languages = [...new Set([language, ...(extraVoices ? variants.map((v) => v.language) : [])])]
  const unmapped = splitLanguages && languages.some((lang) => !(languageBridges[lang] || []).some((id) => autoSelected.includes(id)))
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
          <summary>{t(editing ? 'videoSchedule.edit' : 'videoSchedule.new')}</summary>
          <form onSubmit={submit} className="video-create">
            {editing && <p className="muted">{t('videoSchedule.editHint')}</p>}
            <label>{t('video.brief')}<textarea required maxLength={4000} value={brief} onChange={(e) => setBrief(e.target.value)} rows={3} /></label>
            {!folderMode && <label>{t('video.addMaterials')}<input type="file" multiple accept="image/png,image/jpeg,image/webp,video/mp4,video/webm" onChange={upload} disabled={busy} /></label>}
            {caps.autopilot_enabled && <label><input type="checkbox" checked={folderMode} onChange={(e) => { setFolderMode(e.target.checked); if (e.target.checked) { setManual(false); setMaterials([]) } }} />{t('videoSchedule.folderMode')}</label>}
            {folderMode && <><label>{t('videoLibrary.folder')}<select value={folder} onChange={(e) => setFolder(e.target.value)}>{[...new Set(['', ...folders, folder])].map((name) => <option key={name} value={name}>{name || t('videoLibrary.noFolder')}</option>)}</select></label><label>{t('videoSchedule.materialLimit')}<input type="number" min="1" max="8" value={materialLimit} onChange={(e) => setMaterialLimit(e.target.value)} /></label><p className="muted">{t('videoSchedule.folderHint')}</p></>}
            {!folderMode && <VideoMaterialPicker workspaceId={workspaceId} disabled={busy || materials.length >= 8} onChoose={(item) => setMaterials((old) => old.some((s) => s.asset_id === item.asset_id) ? old : [...old, item])} />}
            {!folderMode && <p className="muted">{t(caps.autopilot_enabled && !manual ? 'video.autoMaterialHint' : 'video.materialHint')}</p>}
            {materials.map((item, index) => <div className="inline-actions" key={`${index}-${item.asset_id}`}><span>{item.name || t('video.scene', { number: index + 1 })}</span><button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => setMaterials((old) => old.filter((_, i) => i !== index))}>{t('common.delete')}</button></div>)}
            <label>{t('videoSchedule.frequency')}<select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
              {frequency === 'custom' && <option value="custom">{t('videoSchedule.custom')}</option>}<option value="daily">{t('topicScout.schedule.daily')}</option><option value="weekdays">{t('topicScout.schedule.weekdays')}</option><option value="weekly">{t('topicScout.schedule.weekly')}</option>
            </select></label>
            {frequency === 'custom' && <label>{t('videoSchedule.cron')}<input required value={customCron} onChange={(e) => setCustomCron(e.target.value)} /></label>}{frequency === 'weekly' && <label>{t('videoSchedule.weekday')}<select value={weekday} onChange={(e) => setWeekday(e.target.value)}>{['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'].map((day, index) => <option key={day} value={String((index + 1) % 7)}>{t(`weekday.${day}`)}</option>)}</select></label>}
            {frequency !== 'custom' && <label>{t('videoSchedule.time')}<input type="time" required value={time} onChange={(e) => setTime(e.target.value)} /></label>}
            <label>{t('videoSchedule.timezone')}<select aria-label={t('videoSchedule.timezone')} value={timezone} onChange={(e) => setTimezone(e.target.value)}>{getAllTimezoneSelectOptions(t, timezone).map((zone) => <option key={zone.value} value={zone.value}>{zone.label}</option>)}</select></label>
            <p className="muted">{t(autoPublish ? 'videoAuto.enabledHint' : 'videoSchedule.draftOnly')}</p>
            <details><summary>{t('video.options')}</summary>
              {!materials.some((scene) => scene.speech_asset_id) && <label>{t('videoVoice.language')}<select value={language} onChange={(e) => setLanguage(e.target.value)}>{['ru', 'en'].map((value) => <option key={value} value={value}>{t(`videoVoice.language.${value}`)}</option>)}</select></label>}
              {!editing && channels.length > 1 && <label>{t('videoSchedule.context')}<select value={channelId} onChange={(e) => setChannelId(e.target.value)}>{channels.map((channel) => <option value={channel.id} key={channel.id}>{channel.title}</option>)}</select></label>}
              {!folderMode && caps.autopilot_enabled && !materials.some((scene) => scene.speech_asset_id) && <label><input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} />{t('video.manualOnly')}</label>}
              {caps.autopilot_enabled && !manual && <>
                {caps.video_generation_enabled && <label>{t('videoGeneration.visuals')}<select value={generatedVisuals} onChange={(e) => setGeneratedVisuals(e.target.value)}><option value="images">{t('videoGeneration.images')}</option><option value="video">{t('videoGeneration.video')}</option></select></label>}
                {caps.speech_enabled && <>
                  <label><input type="checkbox" checked={narration} onChange={(e) => setNarration(e.target.checked)} />{t('videoVoice.enabled')}</label>
                  {narration && <><label>{t('videoVoice.voice')}<select value={voice} onChange={(e) => setVoice(e.target.value)}>{caps.speech_voices.map((value) => <option key={value} value={value}>{t(`videoVoice.voice.${value}`)}</option>)}</select></label><VoicePreview language={language} voice={voice} /></>}
                </>}
                <label>{t('video.targetDuration')}<select value={duration} onChange={(e) => setDuration(e.target.value)}>{[10, 15, 20, 30, 45].map((seconds) => <option key={seconds} value={seconds}>{t('video.seconds', { seconds })}</option>)}</select></label>
                <label>{t('video.style')}<input maxLength={500} value={style} onChange={(e) => setStyle(e.target.value)} /></label>
                <label>{t('videoBatch.count')}<input type="number" min="1" max="5" value={count} onChange={(e) => setCount(Number(e.target.value))} /></label>
              </>}
              {caps.speech_enabled && <>
                <label><input type="checkbox" checked={extraVoices} onChange={(e) => setExtraVoices(e.target.checked)} />{t('videoBatch.extraVoices')}</label>
                {extraVoices && <VideoVariantChoices value={variants} onChange={setVariants} disabled={busy} />}
              </>}
              {!isSelfhostMode() && <label><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />{t('videoSchedule.notify')}</label>}
            </details>
            <label><input type="checkbox" checked={autoPublish} onChange={(e) => setAutoPublish(e.target.checked)} />{t('videoAuto.toggle')}</label>
            {autoPublish && <fieldset><legend>{t('videoAuto.title')}</legend>
              <p className="muted">{t('videoAuto.hint')}</p>
              {autoBridges.map((bridge) => <label key={bridge.id}><input type="checkbox" checked={autoSelected.includes(bridge.id)} onChange={(e) => { setAutoConfirmed(false); setAutoSelected((old) => e.target.checked ? [...old, bridge.id] : old.filter((id) => id !== bridge.id)) }} />{bridge.title} · {bridge.platform} · {bridge.options.visibility || bridge.format}</label>)}
              {!autoBridges.length && <p>{t('videoAuto.noBridges')}</p>}
              <label>{t('videoAuto.caption')}<textarea required maxLength={1024} value={autoCaption} onChange={(e) => { setAutoCaption(e.target.value); setAutoConfirmed(false) }} /></label>
            <><label><input type="checkbox" checked={splitLanguages} onChange={(e) => { setSplitLanguages(e.target.checked); setAutoConfirmed(false) }} />{t('videoSchedule.routeLanguages')}</label>{splitLanguages && languages.map((lang) => <fieldset key={lang}><legend>{t(`videoVoice.language.${lang}`)}</legend>{autoBridges.filter((b) => autoSelected.includes(b.id)).map((b) => <label key={b.id}><input type="checkbox" checked={(languageBridges[lang] || []).includes(b.id)} onChange={(e) => { setAutoConfirmed(false); setLanguageBridges((old) => ({ ...old, [lang]: e.target.checked ? [...(old[lang] || []), b.id] : (old[lang] || []).filter((id) => id !== b.id) })) }} />{b.title}</label>)}</fieldset>)}</>
              <label><input type="checkbox" required checked={autoConfirmed} onChange={(e) => setAutoConfirmed(e.target.checked)} />{t('videoAuto.consent', { count: (!manual ? count : 1) * (extraVoices ? variants.length + 1 : 1) })}</label>
            </fieldset>}
            {(!manual && count > 1 || extraVoices && variants.length > 0) && <p>{t('videoAuto.count', { count: (!manual ? count : 1) * (extraVoices ? variants.length + 1 : 1) })}</p>}
            {!channelId && <p className="muted">{t('topicScout.errors.noChannel')}</p>}
            <button className="btn" disabled={busy || !brief.trim() || !channelId || autoPublish && (!autoConfirmed || !autoSelected.length || !autoCaption.trim() || unmapped) || (!(caps.autopilot_enabled && !manual) && !materials.length)}>{t(editing ? 'videoSchedule.saveChanges' : 'videoSchedule.save')}</button>{editing && <button type="button" className="btn btn-secondary" onClick={() => { setEditing(null); setExpanded(false) }}>{t('common.cancel')}</button>}
          </form>
        </details>}
        {!caps?.supports_schedules && <p>{t('video.unavailable')}</p>}
        {!tasks.length && <p className="muted">{t('videoSchedule.empty')}</p>}
        {shownTasks.map((task) => <section className="card" key={task.id}>
          <h2>{task.goal_text}</h2>{task.task_config?.auto_publication && <p>{t('videoAuto.activePolicy')}</p>}<p>{t(`topicScout.taskStatus.${task.status}`)} · {task.timezone}</p>
          <p className="muted">{t('videoSchedule.nextRun', { date: date(task.next_run_at) })}</p>
          {task.status !== 'archived' && <div className="inline-actions"><button className="btn btn-secondary" disabled={busy} onClick={() => editTask(task)}>{t('videoSchedule.edit')}</button>
            <button className="btn" disabled={busy || !caps?.enabled || runs.some((run) => run.agent_task_id === task.id && activeRun(run))} onClick={() => runNow(task)}>{t('videoSchedule.runNow')}</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => perform(async () => { await (task.status === 'paused' ? resumeAgentTask : pauseAgentTask)(workspaceId, task.id); await refresh() })}>{t(task.status === 'paused' ? 'videoSchedule.resume' : 'videoSchedule.pause')}</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => perform(async () => { await deleteAgentTask(workspaceId, task.id); await refresh() })}>{t('videoSchedule.archive')}</button>
          </div>}
          <p className="muted">{t('videoSchedule.pauseHint')}</p>
          <ul>{runs.filter((run) => run.agent_task_id === task.id).slice(0, 10).map((run) => <li key={run.id}>
            {date(run.created_at)} · {status(run)}{' '}
            {run.video_execution?.auto_publication?.status && <span>{t(`videoAuto.status.${run.video_execution.auto_publication.status}`)}{' '}{run.video_execution.auto_publication.error_code && t(`api.error.${run.video_execution.auto_publication.error_code}`, { defaultValue: run.video_execution.auto_publication.error_code })}{' '}</span>}
            {run.error_code && run.error_code !== 'VIDEO_SCHEDULE_OVERLAP' && <span>{t(`api.error.${run.error_code}`, { defaultValue: t('videoSchedule.run.failed') })}{' '}</span>}
            {run.video_execution?.project_id && <Link to={`/workspaces/${workspaceId}/content/videos/${run.video_execution.project_id}`}>{t('video.open')}</Link>}{' '}
            {run.video_execution?.batch && <span>{t('videoBatch.progress', { done: run.video_execution.batch.items.filter((i) => i.status === 'succeeded').length, total: run.video_execution.batch.items.length })}{' '}{run.video_execution.batch.items.filter((i) => i.project_id && i.project_id !== run.video_execution.project_id).map((item) => <span key={item.id}><Link to={`/workspaces/${workspaceId}/content/videos/${item.project_id}`}>{t('videoBatch.variant', { number: item.position + 1 })}</Link>{' '}</span>)}</span>}
            <Link to={`/workspaces/${workspaceId}/agents/runs/${run.id}`}>{t('videoSchedule.runDetails')}</Link>
          </li>)}</ul>
        </section>)}
        <Link to={`/workspaces/${workspaceId}/content/videos`}>{t('videoSchedule.openStudio')}</Link>
      </div>}
    </AgentSectionLayout>
  </AppShell>
}
