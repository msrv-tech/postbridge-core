import VoicePreview from '../components/VoicePreview'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import { useAuth } from '../useAuth'
import { isSelfhostMode } from '../adapters/runtime'
import { clearToken } from '../adapters/sessionToken'
import { cancelVideo, createVideo, getVideo, getVideoCapabilities, listVideos, reviseVideo, uploadVideoMaterial, prepareVideoPost } from '../adapters/video'
import { useI18n } from '../i18n'
import './VideoStudio.css'
import VideoMaterialPicker from '../components/VideoMaterialPicker'

import { createVideoVoiceVariant, createVideoVoiceBatch, createVideoBatch } from '../adapters/video'
import VideoVariantChoices from '../components/VideoVariantChoices'
import VideoBatches from '../components/VideoBatches'
import VideoBudgetPreview, { estimateVideoUnits } from '../components/VideoBudgetPreview'
import { videoSceneInput as sceneInput } from '../videoSceneInput'
const displaySeconds = (value) => typeof value === 'number' ? Number(value.toFixed(3)) : value
const working = (project) => ['queued', 'running'].includes(project?.current?.status)
const failureKey = (current) => current.usage?.uncertain ? 'video.reconciliationHint'
  : ['VIDEO_BUDGET_EXHAUSTED', 'VIDEO_BUDGET_NOT_AUTHORIZED', 'VIDEO_RENDERER_UNAVAILABLE', 'VIDEO_SPEECH_TOO_LONG', 'VIDEO_SPEECH_UNAVAILABLE', 'VIDEO_SPEECH_ASSET_INVALID', 'VIDEO_INVALID_TRANSLATION'].includes(current.error_code)
    ? `api.error.${current.error_code}` : 'video.failedHint'

export default function VideoStudio() {
  const { workspaceId, projectId } = useParams()
  const [query] = useSearchParams()
  const requestedVersion = Number(query.get("version"))
  const { t, locale } = useI18n()
  const { user } = useAuth()
  const navigate = useNavigate()
  const [capabilities, setCapabilities] = useState(null)
  const [projects, setProjects] = useState([])
  const [project, setProject] = useState(null)
  const [scenes, setScenes] = useState([])
  const [brief, setBrief] = useState('')
  const [materials, setMaterials] = useState([])
  const [manual, setManual] = useState(false)
  const [duration, setDuration] = useState(20)
  const [style, setStyle] = useState('')
  const [generatedVisuals, setGeneratedVisuals] = useState('images')
  const [template, setTemplate] = useState('product_demo')
  const [brandName, setBrandName] = useState('')
  const [brandColor, setBrandColor] = useState('#38bdf8')
  const [narration, setNarration] = useState(true)
  const [voice, setVoice] = useState('Kore')
  const [voiceStyle, setVoiceStyle] = useState('neutral')
  const [selectedVersion, setSelectedVersion] = useState(null)
  const [voiceForm, setVoiceForm] = useState(false)
  const [voiceLanguage, setVoiceLanguage] = useState('ru')
  const [batchCount, setBatchCount] = useState(1)
  const [variants, setVariants] = useState([])
  const [batchVoices, setBatchVoices] = useState(false)
  const [batchBudget, setBatchBudget] = useState('')
  const [batchRefresh, setBatchRefresh] = useState(0)
  const [editRequest, setEditRequest] = useState('')
  const [preparingPost, setPreparingPost] = useState(false)
  const [postCaption, setPostCaption] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const route = `${workspaceId}/${projectId || ''}`
  const currentRoute = useRef(route)
  currentRoute.current = route
  const requestKey = useRef(null)
  const base = `/workspaces/${workspaceId}/content/videos`
  const isNew = projectId === 'new'
  const current = project?.revisions.find((revision) => revision.version === selectedVersion) || project?.current
  const viewProject = project ? { ...project, current } : null
  const producing = project?.revisions.some((revision) => ['queued', 'running'].includes(revision.status))
  const unsavedChanges = !!current && JSON.stringify(scenes.map(sceneInput)) !== JSON.stringify(current.scenes.map(sceneInput)) || !!current && (brandName !== (current.brand_name || '') || brandColor !== (current.brand_color || '#38bdf8'))

  useEffect(() => {
    const provideContext = (event) => {
      if (event.detail?.workspaceId === workspaceId && (event.detail?.projectId === projectId || isNew && !event.detail?.projectId)) {
        event.detail.hasUnsavedChanges = unsavedChanges
        event.detail.selectedVersion = current?.version
        event.detail.materialIds = materials.map((item) => item.asset_id)
        event.detail.brief = brief
      }
    }
    window.addEventListener('postbridge-video-draft-context', provideContext)
    return () => window.removeEventListener('postbridge-video-draft-context', provideContext)
  }, [workspaceId, projectId, isNew, unsavedChanges, current?.version, materials, brief])

  useEffect(() => {
    setOffset(0)
    setProject(null)
    setProjects([])
    setHasMore(false)
    setMaterials([])
    setScenes([])
    setBrief('')
    setManual(false)
    setDuration(20)
    setStyle(''); setTemplate('product_demo'); setBrandName(''); setBrandColor('#38bdf8')
    setGeneratedVisuals('images')
    setNarration(true); setVoice('Kore'); setVoiceStyle('neutral'); setSelectedVersion(Number.isInteger(requestedVersion) && requestedVersion > 0 ? requestedVersion : null); setVoiceForm(false)
    setBatchCount(1); setVariants([]); setBatchVoices(false); setBatchBudget(''); setBatchRefresh(0)
    setEditRequest('')
    setPreparingPost(false)
    setPostCaption('')
    setCapabilities(null)
    setBusy(false)
    requestKey.current = null
  }, [workspaceId, projectId, requestedVersion])

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    async function load() {
      try {
        const caps = await getVideoCapabilities(workspaceId)
        if (!active) return
        setCapabilities(caps)
        if (isNew) return
        if (projectId) {
          const value = await getVideo(workspaceId, projectId)
          if (active) setProject(value)
        } else {
          const value = await listVideos(workspaceId, offset)
          if (active) { setProjects(value.items || []); setHasMore(value.has_more) }
        }
      } catch (e) { if (active) setError(e.message) }
      finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [workspaceId, projectId, isNew, offset, requestedVersion])

  useEffect(() => {
    if (!project) return
    setScenes(current.scenes.map((s) => ({ ...s })))
    setVoice(current.voice || 'Kore'); setVoiceStyle(current.voice_style || 'neutral')
    setVoiceLanguage(current.language || 'ru')
    setBrandName(current.brand_name || ''); setBrandColor(current.brand_color || '#38bdf8')
    setVoiceForm(false)
    requestKey.current = null
  }, [project?.id, current?.version, current?.status])

  useEffect(() => {
    setPreparingPost(false)
    setPostCaption('')
  }, [project?.id, current?.version])

  useEffect(() => {
    if (!producing) return
    let active = true
    let inFlight = false
    const timer = setInterval(async () => {
      if (inFlight) return
      inFlight = true
      try {
        const value = await getVideo(workspaceId, projectId)
        if (active) { setProject(value); setError('') }
      } catch (e) { if (active) setError(e.message) }
      finally { inFlight = false }
    }, 4000)
    return () => { active = false; clearInterval(timer) }
  }, [workspaceId, projectId, producing])

  const run = async (action) => {
    if (busy) return
    setBusy(true)
    setError('')
    try { await action() }
    catch (e) {
      if (currentRoute.current === route) {
        setError(e.message)
        if (e.status >= 400 && e.status < 500) requestKey.current = null
      }
    }
    finally { if (currentRoute.current === route) setBusy(false) }
  }

  useEffect(() => {
    let active = true
    const refresh = async (event) => {
      if (event.detail?.workspaceId !== workspaceId || event.detail?.project_id !== projectId) return
      try {
        if (!projectId) {
          const value = await listVideos(workspaceId, offset)
          if (active) { setProjects(value.items || []); setHasMore(value.has_more) }
          return
        }
        const value = await getVideo(workspaceId, projectId)
        if (active) { setProject(value); if (event.detail.version) setSelectedVersion(event.detail.version) }
      } catch (e) { if (active) setError(e.message) }
    }
    window.addEventListener('postbridge-video-updated', refresh)
    return () => { active = false; window.removeEventListener('postbridge-video-updated', refresh) }
  }, [workspaceId, projectId, offset])

  const upload = (event) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    run(async () => {
      const count = isNew ? materials.length : scenes.length
      if (count + files.length > capabilities.max_scenes) throw new Error(t('video.tooMany'))
      for (const file of files) {
        if (file.size > capabilities.max_upload_bytes) throw new Error(t('video.tooLarge'))
      }
      for (const file of files) {
        const value = await uploadVideoMaterial(workspaceId, file)
        if (currentRoute.current !== route) return
        const item = { asset_id: value.media_asset_id, url: value.url, kind: value.kind, name: file.name, duration_seconds: 5, caption: '' }
        if (isNew) setMaterials((previous) => [...previous, item])
        else setScenes((previous) => [...previous, item])
      }
    })
  }

  const submit = (event) => {
    event.preventDefault()
    run(async () => {
      const body = { brief: isNew ? brief.trim() : project.brief, scenes: (isNew ? materials : scenes).map(sceneInput),
        ...(brandName.trim() ? { brand_name: brandName.trim(), brand_color: brandColor } : {}),
        ...(!isNew ? { language: current.language || 'ru', voice: current.voice || 'Kore', voice_style: current.voice_style || 'neutral',
          ...(scenes.some((scene) => scene.narration_text?.trim() && !scene.speech_asset_id) ? { narration_enabled: true } : {}) } : {}),
        ...(isNew && capabilities.autopilot_enabled && !manual ? {
          mode: 'autopilot', language: locale === 'en' ? 'en' : 'ru', duration_seconds: Number(duration), style, template,
          ...(generatedVisuals === 'video' ? { generated_visuals: 'video' } : {}),
          ...(capabilities.speech_enabled && narration ? { narration_enabled: true, voice, voice_style: voiceStyle } : {}),
        } : {}) }
      // Reuse the same key after network errors, but never for changed inputs.
      const batch = isNew && ((body.mode === 'autopilot' && batchCount > 1) || batchVoices && variants.length > 0)
      const batchOptions = { count: body.mode === 'autopilot' ? Number(batchCount) : 1,
        variants: batchVoices ? variants : [], ...(batchBudget ? { max_units_total: Number(batchBudget) } : {}) }
      const fingerprint = JSON.stringify({ body, ...(batch ? batchOptions : {}) })
      if (requestKey.current?.fingerprint !== fingerprint) requestKey.current = { fingerprint, key: crypto.randomUUID() }
      body.request_key = requestKey.current.key
      if (batch) {
        await createVideoBatch(workspaceId, { video: body, request_key: body.request_key, ...batchOptions })
        if (currentRoute.current === route) navigate(base)
        return
      }
      const value = isNew ? await createVideo(workspaceId, body)
        : await reviseVideo(workspaceId, projectId, { ...body, expected_version: project.current_version })
      if (currentRoute.current !== route) return
      if (isNew) navigate(`${base}/${value.id}`)
      else { setSelectedVersion(value.current_version); setProject(value); setScenes(value.current.scenes.map((s) => ({ ...s }))) }
    })
  }

  const updateScene = (index, change) => setScenes((old) => old.map((s, i) => i === index ? { ...s, ...change } : s))
  const submitVoice = (event) => {
    event.preventDefault()
    run(async () => {
      const body = { expected_version: project.current_version, source_version: current.version,
        ...(batchVoices ? { variants, ...(batchBudget ? { max_units_total: Number(batchBudget) } : {}) }
          : { language: voiceLanguage, voice, voice_style: voiceStyle }) }
      const fingerprint = JSON.stringify(body)
      if (requestKey.current?.fingerprint !== fingerprint) requestKey.current = { fingerprint, key: crypto.randomUUID() }
      const value = await (batchVoices ? createVideoVoiceBatch : createVideoVoiceVariant)(workspaceId, project.id, { ...body, request_key: requestKey.current.key })
      if (currentRoute.current !== route) return
      if (batchVoices) { setBatchRefresh((n) => n + 1); setVoiceForm(false); return }
      setSelectedVersion(value.requested_revision?.version || value.current_version); setProject(value); setVoiceForm(false)
    })
  }
  const moveScene = (index, delta) => setScenes((old) => {
    const copy = [...old]
    ;[copy[index], copy[index + delta]] = [copy[index + delta], copy[index]]
    return copy
  })
  const askAssistant = () => window.dispatchEvent(new CustomEvent('postbridge-assistant-ask', { detail: {
    workspaceId, question: t('video.askQuestion'), projectId: project.id,
  } }))
  const requestEdit = (event) => {
    event.preventDefault()
    if (!editRequest.trim()) return
    window.dispatchEvent(new CustomEvent('postbridge-assistant-ask', { detail: {
      workspaceId, projectId: project.id, question: editRequest.trim(),
    } }))
    setEditRequest('')
  }
  const preparePost = (event) => {
    event.preventDefault()
    run(async () => {
      const result = await prepareVideoPost(workspaceId, project.id, {
        expected_version: project.current_version, caption: postCaption.trim(),
        ...(current.version !== project.current_version ? { source_version: current.version } : {}),
      })
      if (currentRoute.current !== route) return
      if (result.content_item_id) navigate(`/workspaces/${workspaceId}/content/${result.content_item_id}`)
      else {
        const value = await getVideo(workspaceId, projectId)
        if (currentRoute.current !== route) return
        setPreparingPost(false)
        setProject(value)
      }
    })
  }

  return <AppShell title={t('video.title')} subtitle={t('video.subtitle')} user={user}
    onLogout={() => { clearToken(); navigate('/') }} actions={<Link className="btn btn-secondary btn-small" to={projectId ? base : `/workspaces/${workspaceId}/content`}>{t('common.back')}</Link>}>
    <div className="video-studio">
      {error && <p className="error" role="alert">{error}</p>}
      {loading ? <p role="status">{t('common.loading')}</p> : !capabilities?.enabled && (isNew || !project && !projects.length) ? <div className="card"><p>{t('video.unavailable')}</p></div>
        : isNew ? <form className="card video-create" onSubmit={submit}>
          <h2>{t('video.new')}</h2>
          <label>{t('video.brief')}<textarea required maxLength={4000} rows={3} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder={t('video.placeholder')} /></label>
          <label className="video-upload">{t('video.addMaterials')}<input type="file" accept="image/png,image/jpeg,image/webp,video/mp4,video/webm" multiple onChange={upload} disabled={busy} /></label>
          <VideoMaterialPicker workspaceId={workspaceId} disabled={busy || materials.length >= capabilities.max_scenes} onChoose={(item) => setMaterials((old) => old.some((s) => s.asset_id === item.asset_id) ? old : [...old, item])} />
          <p className="muted">{t(capabilities.autopilot_enabled && !manual ? 'video.autoMaterialHint' : 'video.materialHint')}</p>
          {materials.length > 0 && <ul className="video-materials">{materials.map((item) => <li key={item.asset_id}><span>{item.name}</span><button className="btn btn-secondary btn-small" type="button" disabled={busy} onClick={() => setMaterials((old) => old.filter((s) => s !== item))}>{t('common.delete')}</button></li>)}</ul>}
          {capabilities.autopilot_enabled && <details><summary>{t('video.options')}</summary>
            {!manual && <label>{t('videoStoryboard.template')}<select value={template} disabled={busy} onChange={(e) => setTemplate(e.target.value)}>{['problem_solution', 'product_demo', 'tutorial'].map((value) => <option key={value} value={value}>{t(`videoStoryboard.template.${value}`)}</option>)}</select></label>}
            <label>{t('videoStoryboard.brandName')}<input maxLength={80} value={brandName} onChange={(e) => setBrandName(e.target.value)} disabled={busy} /></label>
            {brandName.trim() && <label>{t('videoStoryboard.brandColor')}<input type="color" value={brandColor} onChange={(e) => setBrandColor(e.target.value)} disabled={busy} /></label>}
            <label><input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} disabled={busy} />{t('video.manualOnly')}</label>
            {!manual && <>
              {capabilities.video_generation_enabled && <label>{t('videoGeneration.visuals')}<select value={generatedVisuals} onChange={(e) => setGeneratedVisuals(e.target.value)} disabled={busy}><option value="images">{t('videoGeneration.images')}</option><option value="video">{t('videoGeneration.video')}</option></select></label>}
              <label>{t('video.targetDuration')}<select value={duration} onChange={(e) => setDuration(e.target.value)} disabled={busy}>{[10, 15, 20, 30, 45].map((seconds) => <option value={seconds} key={seconds}>{t('video.seconds', { seconds })}</option>)}</select></label>
              <label>{t('video.style')}<input type="text" value={style} maxLength={500} onChange={(e) => setStyle(e.target.value)} disabled={busy} /></label>
              {capabilities.speech_enabled && <>
                <label><input type="checkbox" checked={narration} onChange={(e) => setNarration(e.target.checked)} disabled={busy} />{t('videoVoice.enabled')}</label>
                {narration && <><label>{t('videoVoice.voice')}<select value={voice} onChange={(e) => setVoice(e.target.value)}>{capabilities.speech_voices.map((name) => <option key={name} value={name}>{t(`videoVoice.voice.${name}`)}</option>)}</select></label><VoicePreview language={locale === 'en' ? 'en' : 'ru'} voice={voice} /></>}
              </>}
            </>}
            {!manual && <label>{t('videoBatch.count')}<input type="number" min="1" max="5" value={batchCount} disabled={busy} onChange={(e) => setBatchCount(Number(e.target.value))} /></label>}
            {capabilities.speech_enabled && <>
              <label><input type="checkbox" checked={batchVoices} disabled={busy} onChange={(e) => setBatchVoices(e.target.checked)} />{t('videoBatch.extraVoices')}</label>
              {batchVoices && <VideoVariantChoices value={variants} onChange={setVariants} disabled={busy} />}
            </>}
            {(!manual && batchCount > 1 || batchVoices) && <label>{t('videoBatch.budget')}<input type="number" min="1" max="100000000" value={batchBudget} onChange={(e) => setBatchBudget(e.target.value)} placeholder={t('videoSchedule.planBudget')} /></label>}
          </details>}
          {(!manual && batchCount > 1 || batchVoices && variants.length > 0) && <p>{t('videoAuto.count', { count: (!manual ? batchCount : 1) * (batchVoices ? variants.length + 1 : 1) })}</p>}
          <VideoBudgetPreview units={estimateVideoUnits(capabilities, { mode: capabilities.autopilot_enabled && !manual ? 'autopilot' : 'manual', generated_visuals: generatedVisuals,
            duration_seconds: Number(duration), scenes: materials, narration_enabled: !manual && narration && capabilities.speech_enabled }, !manual ? batchCount : 1, batchVoices ? variants : [])} limit={(!manual && batchCount > 1 || batchVoices && variants.length > 0) ? batchBudget : null} />
          <button className="btn" disabled={busy || !brief.trim()}>{busy ? t('video.working') : capabilities.autopilot_enabled && !manual ? t('video.createAuto') : materials.length ? t('video.assemble') : t('video.saveIdea')}</button>
        </form> : project ? <div className="video-project-layout">
          <section className="card video-preview">
            <h2>{project.brief}</h2>
            {project.revisions.length > 1 && <label>{t('videoVoice.version')}<select value={current.version} disabled={busy || unsavedChanges} onChange={(e) => setSelectedVersion(Number(e.target.value))}>{project.revisions.map((revision) => <option key={revision.id} value={revision.version}>{t('video.version', { version: revision.version })} · {t(`videoVoice.language.${revision.language || 'ru'}`)} · {t(`video.status.${revision.status}`)}</option>)}</select></label>}
            <p role="status">{t(`video.status.${current.status}`)}{working(viewProject) && ` · ${t(`video.stage.${current.stage}`)}`}</p>
            {current.output_url && <video key={current.output_url} src={current.output_url} controls playsInline preload="metadata" aria-label={t('video.preview')} />}
            {current.status === 'draft' && <p className="muted">{t('video.draftHint')}</p>}
            {current.error_code && <p className="error">{t(failureKey(current))}</p>}
            {current.usage?.tokens_charged > 0 && <p className="muted">{t('video.usage', { units: current.usage.tokens_charged })}</p>}
            <div className="inline-actions">
              {current.output_url && <a className="btn" href={`${current.output_url}?download=1`} download={`postbridge-${project.id}.mp4`} target="_blank" rel="noreferrer">{t('video.download')}</a>}
              {current.post_draft?.content_item_id && <Link className="btn" to={`/workspaces/${workspaceId}/content/${current.post_draft.content_item_id}`}>{t('videoPost.open')}</Link>}
              {capabilities.enabled && current.status === 'succeeded' && !current.post_draft && !preparingPost && <button className="btn" disabled={busy || unsavedChanges} onClick={() => setPreparingPost(true)}>{t('videoPost.prepare')}</button>}
              {working(viewProject) && <button className="btn btn-secondary" disabled={busy} onClick={() => run(async () => { const value = await cancelVideo(workspaceId, project.id, project.current_version, current.version !== project.current_version ? current.version : undefined); if (currentRoute.current === route) setProject(value) })}>{t('common.cancel')}</button>}
              {!isSelfhostMode() && <button className="btn btn-secondary" onClick={askAssistant}>{t('video.askAssistant')}</button>}
              {capabilities.supports_schedules && !working(viewProject) && !unsavedChanges && <Link className="btn btn-secondary" to={`/workspaces/${workspaceId}/agents/videos?source=${project.id}&version=${current.version}`}>{t('videoSchedule.repeat')}</Link>}
            </div>
            {capabilities.enabled && current.status === 'succeeded' && !current.post_draft && unsavedChanges && <p className="muted">{t('videoPost.unsaved')}</p>}
            {current.post_draft?.status === 'deleted' && <p className="muted">{t('videoPost.deleted')}</p>}
            {preparingPost && !current.post_draft && <form className="video-post-draft" onSubmit={preparePost}>
              <h3>{t('videoPost.version', { version: current.version })}</h3>
              <p className="muted">{t('videoPost.hint')}</p>
              <label>{t('videoPost.caption')}<textarea rows={3} maxLength={4000} value={postCaption} onChange={(e) => setPostCaption(e.target.value)} disabled={busy} /></label>
              {unsavedChanges && <p className="muted">{t('videoPost.unsaved')}</p>}
              <div className="inline-actions">
                <button className="btn" disabled={busy || unsavedChanges}>{busy ? t('video.working') : t('videoPost.create')}</button>
                <button className="btn btn-secondary" type="button" disabled={busy} onClick={() => setPreparingPost(false)}>{t('common.cancel')}</button>
              </div>
            </form>}
            {project.origin && <p><Link to={`/workspaces/${workspaceId}/agents/videos?task=${project.origin.agent_task_id}`}>{t('videoSchedule.sourceTask')}</Link>{' · '}<Link to={`/workspaces/${workspaceId}/agents/runs/${project.origin.agent_run_id}`}>{t('videoSchedule.runDetails')}</Link></p>}
            {!isSelfhostMode() && capabilities.enabled && !working(viewProject) && current.scenes.length > 0 && <form onSubmit={requestEdit}>
              <label>{t('video.whatToChange')}<input type="text" value={editRequest} maxLength={4000} onChange={(e) => setEditRequest(e.target.value)} placeholder={t('video.editPlaceholder')} /></label>
              {unsavedChanges && <p className="muted">{t('api.error.VIDEO_UNSAVED_SCENES')}</p>}
              <button className="btn btn-secondary" disabled={!editRequest.trim() || unsavedChanges}>{t('video.applyEdit')}</button>
            </form>}
            {capabilities.enabled && capabilities.speech_enabled && current.status === 'succeeded' && <div className="video-voice">
              {!voiceForm ? <div className="inline-actions">
                <button className="btn btn-secondary" disabled={busy || unsavedChanges} onClick={() => { setVoiceLanguage(current.language === 'en' ? 'ru' : 'en'); setVoiceForm(true) }}>{t('videoVoice.addLanguage')}</button>
                <button className="btn btn-secondary" disabled={busy || unsavedChanges} onClick={() => { setVoiceLanguage(current.language || 'ru'); setVoiceForm(true) }}>{t('videoVoice.change')}</button>
              </div> : <form onSubmit={submitVoice}>
                <p className="muted">{t('videoVoice.reuseHint')}</p>
                <label><input type="checkbox" checked={batchVoices} onChange={(e) => setBatchVoices(e.target.checked)} />{t('videoBatch.multiple')}</label>
                {batchVoices ? <>
                  <VideoVariantChoices value={variants} onChange={setVariants} disabled={busy} />
                  <label>{t('videoBatch.budget')}<input type="number" min="1" max="100000000" value={batchBudget} onChange={(e) => setBatchBudget(e.target.value)} placeholder={t('videoSchedule.planBudget')} /></label>
                </> : <><label>{t('videoVoice.language')}<select value={voiceLanguage} onChange={(e) => setVoiceLanguage(e.target.value)} disabled={busy}>{capabilities.speech_languages.map((language) => <option key={language} value={language}>{t(`videoVoice.language.${language}`)}</option>)}</select></label>
                <label>{t('videoVoice.voice')}<select value={voice} onChange={(e) => setVoice(e.target.value)} disabled={busy}>{capabilities.speech_voices.map((name) => <option key={name} value={name}>{t(`videoVoice.voice.${name}`)}</option>)}</select></label><VoicePreview language={voiceLanguage} voice={voice} />
                <label>{t('videoVoice.style')}<select value={voiceStyle} onChange={(e) => setVoiceStyle(e.target.value)} disabled={busy}>{['neutral', 'warm', 'energetic'].map((style) => <option key={style} value={style}>{t(`videoVoice.style.${style}`)}</option>)}</select></label>
                </>}
                <VideoBudgetPreview units={(capabilities.reservation_units || 0) * (batchVoices ? variants.length : 1)} limit={batchVoices ? batchBudget : null} />
                <div className="inline-actions"><button className="btn" disabled={busy || unsavedChanges || batchVoices && !variants.length}>{t(batchVoices ? 'videoBatch.create' : 'videoVoice.create')}</button><button className="btn btn-secondary" type="button" disabled={busy} onClick={() => setVoiceForm(false)}>{t('common.cancel')}</button></div>
              </form>}
            </div>}
            {capabilities.supports_campaigns && current.status === 'succeeded' && <Link to={`/workspaces/${workspaceId}/content/video-campaigns?project=${project.id}&version=${current.version}`}>{t('videoCampaign.addCurrent')}</Link>}
            <VideoBatches workspaceId={workspaceId} projectId={project.id} refreshKey={batchRefresh} />
            {project.revisions.length > 1 && <details><summary>{t('video.history')}</summary>{project.revisions.map((revision) => <p key={revision.id}>{t('video.version', { version: revision.version })} · {t(`videoVoice.language.${revision.language || 'ru'}`)} · {t(`video.status.${revision.status}`)} {revision.output_url && <a href={`${revision.output_url}?download=1`} target="_blank" rel="noreferrer">{t('video.download')}</a>}{revision.post_draft?.content_item_id && <>{' · '}<Link to={`/workspaces/${workspaceId}/content/${revision.post_draft.content_item_id}`}>{t('videoPost.open')}</Link></>}</p>)}</details>}
          </section>
          {capabilities.enabled && <form className="card video-scenes" onSubmit={submit}>
            <details open={current.status === 'draft'}><summary>{t('video.scenes')}</summary>
              {scenes.map((scene, index) => <fieldset key={`${index}-${scene.asset_id}`} disabled={busy || working(viewProject)}>
                <legend>{t('video.scene', { number: index + 1 })}</legend>
                {scene.preview_url && <img src={scene.preview_url} alt={t('videoStoryboard.preview', { number: index + 1 })} loading="lazy" />}
                {scene.url && !scene.preview_url && (scene.kind === 'video' ? <video src={scene.url} controls preload="metadata" /> : <img src={scene.url} alt={t('video.scene', { number: index + 1 })} loading="lazy" />)}
                <label>{t('video.caption')}<textarea maxLength={180} rows={2} value={scene.caption} onChange={(e) => updateScene(index, { caption: e.target.value })} /></label>
                {scene.narration_text != null && <label>{t('videoVoice.text')}<textarea maxLength={400} rows={2} value={scene.narration_text} onChange={(e) => updateScene(index, { narration_text: e.target.value, speech_asset_id: null, speech_url: null })} /></label>}
                {scene.narration_shortened && <p className="muted">{t('videoVoice.shortened')}</p>}
                {scene.speech_url && <audio src={scene.speech_url} controls preload="metadata" aria-label={t('videoVoice.preview')} />}
                <label>{t('video.duration')}<input required type="number" min="1" max="15" step="any" value={displaySeconds(scene.duration_seconds)} onChange={(e) => updateScene(index, { duration_seconds: e.target.value })} /></label>
                <details className="video-scene-advanced"><summary>{t('videoStoryboard.advanced')}</summary>
                  {scene.visual_description && <p className="muted">{scene.visual_description}</p>}
                  <label>{t('videoStoryboard.purpose')}<select value={scene.purpose || 'demo'} onChange={(e) => updateScene(index, { purpose: e.target.value })}>{['hook', 'demo', 'benefit', 'cta'].map((value) => <option key={value} value={value}>{t(`videoStoryboard.purpose.${value}`)}</option>)}</select></label>
                  {scene.kind === 'video' && <label>{t('videoStoryboard.trimStart')}<input type="number" min="0" max="180" step="any" value={displaySeconds(scene.source_start_seconds || 0)} onChange={(e) => updateScene(index, { source_start_seconds: e.target.value })} /></label>}
                  {scene.kind !== 'video' && <label>{t('videoStoryboard.motion')}<select value={scene.motion || 'none'} onChange={(e) => updateScene(index, { motion: e.target.value })}>{['none', 'zoom_in', 'pan_left', 'pan_right'].map((value) => <option key={value} value={value}>{t(`videoStoryboard.motion.${value}`)}</option>)}</select></label>}
                  <label>{t('videoStoryboard.transition')}<select value={scene.transition || 'cut'} onChange={(e) => updateScene(index, { transition: e.target.value })}>{['cut', 'fade'].map((value) => <option key={value} value={value}>{t(`videoStoryboard.transition.${value}`)}</option>)}</select></label>
                  <details><summary>{t('videoStoryboard.replace')}</summary><VideoMaterialPicker workspaceId={workspaceId} disabled={busy || working(viewProject)} onChoose={(item) => updateScene(index, { asset_id: item.asset_id, url: item.url, kind: item.kind, source_start_seconds: undefined, image_prompt: undefined, regenerate_image: undefined, regenerate_video: undefined, generation_kind: undefined, motion: undefined, preview_url: undefined })} /></details>
                  {(capabilities.generated_media || capabilities.video_generation_enabled) && <>
                    {capabilities.generated_media && <label><input type="checkbox" checked={!!scene.regenerate_image} onChange={(e) => updateScene(index, { regenerate_image: e.target.checked || undefined, regenerate_video: undefined, generation_kind: 'image' })} />{t('videoStoryboard.regenerate')}</label>}
                    {capabilities.video_generation_enabled && <label><input type="checkbox" checked={!!scene.regenerate_video} onChange={(e) => updateScene(index, { regenerate_video: e.target.checked || undefined, regenerate_image: undefined, duration_seconds: e.target.checked ? Math.min(8, Number(scene.duration_seconds)) : scene.duration_seconds, generation_kind: 'video' })} />{t('videoGeneration.regenerate')}</label>}
                    {(scene.regenerate_image || scene.regenerate_video) && <label>{t('videoStoryboard.prompt')}<textarea required maxLength={1200} rows={3} value={scene.image_prompt || ''} onChange={(e) => updateScene(index, { image_prompt: e.target.value })} /></label>}
                    {(scene.regenerate_image || scene.regenerate_video) && <p className="muted">{t('videoStoryboard.reuse')}</p>}
                  </>}
                </details>
                <div className="inline-actions">
                  <button type="button" className="btn btn-secondary btn-small" disabled={index === 0} onClick={() => moveScene(index, -1)}>{t('video.up')}</button>
                  <button type="button" className="btn btn-secondary btn-small" disabled={index === scenes.length - 1} onClick={() => moveScene(index, 1)}>{t('video.down')}</button>
                  <button type="button" className="btn btn-secondary btn-small" onClick={() => setScenes((old) => old.filter((_, i) => i !== index))}>{t('common.delete')}</button>
                </div>
              </fieldset>)}
              <label className="video-upload">{t('video.addMaterials')}<input type="file" accept="image/png,image/jpeg,image/webp,video/mp4,video/webm" multiple disabled={busy || working(viewProject)} onChange={upload} /></label>
            </details>
            <VideoMaterialPicker workspaceId={workspaceId} disabled={busy || working(viewProject) || scenes.length >= capabilities.max_scenes} onChoose={(item) => setScenes((old) => [...old, item])} />
            <details><summary>{t('videoStoryboard.brand')}</summary><label>{t('videoStoryboard.brandName')}<input maxLength={80} value={brandName} onChange={(e) => setBrandName(e.target.value)} disabled={busy || working(viewProject)} /></label>
              {brandName.trim() && <label>{t('videoStoryboard.brandColor')}<input type="color" value={brandColor} onChange={(e) => setBrandColor(e.target.value)} disabled={busy || working(viewProject)} /></label>}
            </details>
            <p className="muted">{t('video.total', { seconds: scenes.reduce((sum, s) => sum + Number(s.duration_seconds || 0), 0).toLocaleString(locale === 'en' ? 'en-US' : 'ru-RU', { maximumFractionDigits: 1 }) })}</p>
            <VideoBudgetPreview units={estimateVideoUnits(capabilities, { scenes, narration_enabled: scenes.some((s) => s.narration_text?.trim() && !s.speech_asset_id) })} />
            <button className="btn" disabled={busy || working(viewProject) || !scenes.length || scenes.reduce((sum, s) => sum + Number(s.duration_seconds || 0), 0) > 45}>{busy ? t('video.working') : t('video.assemble')}</button>
          </form>}
        </div> : <>
          {capabilities.enabled && <div className="video-list-actions"><Link className="btn" to={`${base}/new`}>{t('video.new')}</Link>{capabilities.supports_schedules && <Link className="btn btn-secondary" to={`/workspaces/${workspaceId}/agents/videos`}>{t('videoSchedule.title')}</Link>}</div>}
          {capabilities.supports_campaigns && <p><Link to={`/workspaces/${workspaceId}/content/video-campaigns`}>{t('videoCampaign.title')}</Link></p>}
          <VideoBatches workspaceId={workspaceId} />
          {!projects.length && <div className="card"><h2>{t('video.empty')}</h2><p>{t('video.emptyHint')}</p></div>}
          <div className="video-grid">{projects.map((item) => <Link className="card video-card" key={item.id} to={`${base}/${item.id}`}>
            {item.current.output_url && <video src={item.current.output_url} preload="metadata" muted aria-label={t('video.preview')} />}
            <h2>{item.brief}</h2><p>{t(`video.status.${item.current.status}`)}</p><span>{t('video.open')}</span>
          </Link>)}</div>
          <div className="inline-actions">{offset > 0 && <button className="btn btn-secondary" onClick={() => setOffset((old) => Math.max(0, old - 20))}>{t('common.back')}</button>}{hasMore && <button className="btn btn-secondary" onClick={() => setOffset((old) => old + 20)}>{t('common.next')}</button>}</div>
        </>}
    </div>
  </AppShell>
}
