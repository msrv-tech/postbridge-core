import { Link } from 'react-router-dom'
import { useEffect, useMemo, useRef, useState } from 'react'
import { getVideoPublication, approveVideoPublication, retryVideoPublication, cancelVideoPublication, rescheduleVideoPublication, reconcileVideoPublication } from '../adapters/video'
import { useI18n } from '../i18n'
import { useAuth } from '../useAuth'
import { getAllTimezoneSelectOptions } from '../timezoneOptions'
import { deliveryLocalInput, formatDeliveryTime, resolveDeliveryTime } from '../videoDeliveryTime'

export default function VideoPublicationCard({ workspaceId, postId, dirty, saveDraft, onLocked }) {
  const { t, locale } = useI18n()
  const { user } = useAuth()
  const [view, setView] = useState(null)
  const [reviewing, setReviewing] = useState(false)
  const [selected, setSelected] = useState([])
  const [options, setOptions] = useState({})
  const throughBridges = Array.isArray(view?.bridges)
  const destinations = throughBridges ? view.bridges : view?.destinations || []
  const optionFor = (destination) => (throughBridges ? destination.options : options[destination.id]) || { format: destination.format || 'video', title: '', visibility: 'private', made_for_kids: null, contains_synthetic_media: true }
  const changeOption = (destination, key, value) => setOptions((old) => ({ ...old, [destination.id]: { ...optionFor(destination), [key]: value } }))
  const optionsValid = !destinations.some((destination) => selected.includes(destination.id) && destination.platform === 'youtube' && (!(optionFor(destination).title || '').trim() || optionFor(destination).made_for_kids === null))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [scheduled, setScheduled] = useState(false)
  const [localTime, setLocalTime] = useState('')
  const [timezone, setTimezone] = useState(() => user?.profile_timezone || user?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const [rescheduling, setRescheduling] = useState(false)
  const [scheduleVersion, setScheduleVersion] = useState(null)
  const resolution = useMemo(() => resolveDeliveryTime(localTime, timezone), [localTime, timezone])
  const zones = useMemo(() => getAllTimezoneSelectOptions(t, timezone), [t, timezone])
  const timingValid = !scheduled || resolution.status === 'valid'
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    let inFlight = false
    const refresh = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const result = await getVideoPublication(workspaceId, postId)
        if (alive.current) { setView((old) => old?.approval_id || result.approval_id ? result : old || result); onLocked(Boolean(result.approval_id)) }
      } catch (e) { if (alive.current) setError(e.message) }
      finally { inFlight = false }
    }
    refresh()
    const timer = setInterval(refresh, 4000)
    return () => { alive.current = false; clearInterval(timer) }
  }, [workspaceId, postId, onLocked])
  const run = async (action) => {
    if (busy) return
    setBusy(true); setError('')
    try { await action() } catch (e) { if (alive.current) setError(e.message) }
    finally { if (alive.current) setBusy(false) }
  }
  const review = () => run(async () => {
    const saved = await saveDraft()
    if (!saved || !alive.current) return
    const result = await getVideoPublication(workspaceId, postId)
    if (!alive.current) return
    setView(result); setReviewing(true); onLocked(Boolean(result.approval_id))
  })
  const approve = () => run(async () => {
    const result = await approveVideoPublication(workspaceId, postId, { expected_snapshot: view.snapshot_hash,
      ...(throughBridges ? { bridge_ids: selected, expected_bridges: Object.fromEntries(destinations.filter(destination => selected.includes(destination.id)).map(destination => [destination.id, destination.settings_hash])) }
        : { channel_ids: selected, options: Object.fromEntries(destinations.filter((destination) => selected.includes(destination.id)).map((destination) => [destination.id, destination.platform === 'youtube' ? optionFor(destination) : { format: destination.format || 'video' }])) }),
      ...(scheduled ? { publish_at: resolution.iso, timezone } : {}) })
    if (alive.current) { setView(result); onLocked(Boolean(result.approval_id)) }
  })
  const editTime = () => {
    const zone = view.delivery?.timezone || timezone
    setTimezone(zone)
    setLocalTime(deliveryLocalInput(view.delivery?.publish_at, zone))
    setScheduled(true)
    setScheduleVersion(view.delivery.version)
    setRescheduling(true)
    setError('')
  }
  const reschedule = () => run(async () => {
    const result = await rescheduleVideoPublication(workspaceId, postId, { expected_schedule_version: scheduleVersion,
      ...(scheduled ? { publish_at: resolution.iso, timezone } : {}) })
    if (alive.current) { setView(result); setRescheduling(false) }
  })
  const timeFields = <div className="video-delivery-time">
    <label><input type="checkbox" checked={scheduled} disabled={busy} onChange={(event) => setScheduled(event.target.checked)} />{t('videoPublish.later')}</label>
    {scheduled && <>
      <label>{t('videoPublish.dateTime')}<input type="datetime-local" value={localTime} disabled={busy} onChange={(event) => setLocalTime(event.target.value)} /></label>
      <label>{t('videoPublish.timezone')}<select value={timezone} disabled={busy} onChange={(event) => setTimezone(event.target.value)}>{zones.map((zone) => <option key={zone.value} value={zone.value}>{zone.value.replaceAll('_', ' ')}</option>)}</select></label>
      {localTime && resolution.status !== 'valid' && <p className="error">{t(`videoPublish.time.${resolution.status}`)}</p>}
      {resolution.status === 'valid' && <p>{t('videoPublish.at', { time: formatDeliveryTime(resolution.iso, timezone, locale), zone: timezone })}</p>}
    </>}
    <p className="muted">{t('videoPublish.deadlineHint')}</p>
  </div>
  return <section className="video-publication-card" aria-label={t('videoPublish.title')}>
    <h3>{t('videoPublish.title')}</h3>
    {error && <p className="error" role="alert">{error}</p>}
    {!view ? <p>{t('common.loading')}</p> : view.approval_id ? <>
      <p>{t('videoPublish.locked')}</p>
      {view.delivery?.publish_at && <p>{t('videoPublish.at', { time: formatDeliveryTime(view.delivery.publish_at, view.delivery.timezone, locale), zone: view.delivery.timezone })}</p>}
      {view.enabled && view.delivery && !view.targets.some((target) => ['publishing', 'processing', 'unknown'].includes(target.status)) && view.targets.some((target) => ['pending', 'expired', 'cancelled', 'failed'].includes(target.status)) && !rescheduling && <button type="button" className="btn btn-secondary" disabled={busy} onClick={editTime}>{t('videoPublish.changeTime')}</button>}
      {rescheduling && <>
        {timeFields}
        <p className="muted">{t('videoPublish.rescheduleHint')}</p>
        <button type="button" className="btn" disabled={busy || !timingValid} onClick={reschedule}>{t(scheduled ? 'videoPublish.confirmTime' : 'videoPublish.confirmNow')}</button>{' '}
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setRescheduling(false)}>{t('common.cancel')}</button>
      </>}
      {view.targets.some((target) => target.status === 'pending') && <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => run(async () => { const result = await cancelVideoPublication(workspaceId, postId); if (alive.current) setView(result) })}>{t('videoPublish.cancel')}</button>}
      <ul>{view.targets.map((target) => <li key={target.id}>
        <strong>{target.title}</strong>{' · '}{t(`videoPublish.format.${target.format || 'video'}`)}{' · '}{t(`videoPublish.status.${target.status}`)}
        {target.platform === 'youtube' && target.options?.title && <p>{target.options.title}{' · '}{t(`videoPublish.visibility.${target.options.visibility || 'private'}`)}</p>}
        {target.external_url && <>{' · '}<a href={target.external_url} target="_blank" rel="noreferrer">{t('videoPublish.open')}</a></>}
        {view.enabled && ['youtube', 'instagram'].includes(target.platform) && ['unknown', 'failed'].includes(target.status) && !target.error_code?.startsWith('VIDEO_BRIDGE_') && !['EXTERNAL_API_INSTAGRAM_REJECTED', 'EXTERNAL_API_YOUTUBE_REJECTED'].includes(target.error_code) && <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => run(async () => { const result = await reconcileVideoPublication(workspaceId, postId, target.id); if (alive.current) setView(result) })}>{t('videoPublish.checkResult')}</button>}
        {target.error_code?.startsWith('VIDEO_BRIDGE_') && <Link to={`/workspaces/${workspaceId}/channels`}>{t('videoBridge.manage')}</Link>}
        {target.status === 'unknown' && <p className="muted">{t('videoPublish.unknownHint')}</p>}
        {(target.status === 'expired' || target.error_code === 'VIDEO_DELIVERY_MISSED') && <p className="muted">{t('videoPublish.missedHint')}</p>}
        {target.status === 'failed' && target.error_code !== 'VIDEO_DELIVERY_MISSED' && <p className="error">{t(target.error_code?.startsWith('VIDEO_BRIDGE_') ? `api.error.${target.error_code}` : target.error_code === 'EXTERNAL_API_YOUTUBE_VISIBILITY_MISMATCH' ? 'videoPublish.visibilityMismatch' : 'videoPublish.failedHint')}</p>}
        {view.enabled && (!view.delivery?.deadline_at || new Date(view.delivery.deadline_at).getTime() > Date.now()) && target.status === 'failed' && ['EXTERNAL_API_TELEGRAM_PUBLISH_ERROR', 'EXTERNAL_API_INSTAGRAM_REJECTED', 'EXTERNAL_API_YOUTUBE_REJECTED'].includes(target.error_code) && <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => run(async () => { const result = await retryVideoPublication(workspaceId, postId, target.id); if (alive.current) setView(result) })}>{t('videoPublish.retry')}</button>}
      </li>)}</ul>
      {view.delivery?.history?.length > 1 && <details><summary>{t('videoPublish.timeHistory')}</summary>{view.delivery.history.map((entry) => <p key={entry.version}>{entry.publish_at ? formatDeliveryTime(entry.publish_at, entry.timezone, locale) + ` · ${entry.timezone}` : t('videoPublish.immediate')}</p>)}</details>}
    </> : !view.enabled ? <p className="muted">{t('videoPublish.disabled')}</p> : <>
      <p className="muted">{t('videoPublish.hint')}</p>
      <button type="button" className="btn btn-secondary" disabled={busy} onClick={review}>{busy ? t('common.loading') : t('videoPublish.review')}</button>
      {reviewing && <>
        <p>{t('videoPublish.version', { version: view.snapshot.version })}</p>
        <video className="video-publication-preview" src={view.snapshot.output_url} controls playsInline preload="metadata" aria-label={t('videoPublish.preview')} />
        <pre className="video-publication-text">{view.snapshot.text || t('videoPublish.noCaption')}</pre>
        <fieldset disabled={busy}><legend>{t(throughBridges ? 'videoBridge.routes' : 'videoPublish.destinations')}</legend>
          {destinations.length ? destinations.map((destination) => <label key={destination.id}><input type="checkbox" checked={selected.includes(destination.id)} onChange={(event) => setSelected((old) => event.target.checked ? [...old, destination.id] : old.filter((id) => id !== destination.id))} />{destination.title}{' · '}{destination.platform === 'youtube' ? 'YouTube' : destination.platform === 'instagram' ? 'Instagram' : 'Telegram'}{' · '}{t(`videoPublish.format.${destination.format || 'video'}`)}</label>) : <p>{t(throughBridges ? 'videoBridge.noRoutes' : 'videoPublish.noDestinations')}</p>}
        </fieldset>
        {throughBridges && <>
          <p className="muted">{t('videoBridge.reviewHint')}</p>
          <Link to={`/workspaces/${workspaceId}/channels`}>{t('videoBridge.manage')}</Link>
          {destinations.filter(destination => selected.includes(destination.id) && destination.platform === 'youtube').map(destination => <div key={destination.id} className="video-provider-options">
            <p><strong>{destination.title}</strong>{' · '}{optionFor(destination).title}</p>
            <p>{t(`videoPublish.visibility.${optionFor(destination).visibility}`)}{' · '}{t(optionFor(destination).made_for_kids ? 'videoPublish.kidsAudience' : 'videoPublish.generalAudience')}</p>
          </div>)}
        </>}
        {!throughBridges && destinations.filter((destination) => selected.includes(destination.id) && destination.platform === 'youtube').map((destination) => <fieldset className="video-provider-options" key={destination.id} disabled={busy}>
          <legend>YouTube · {destination.title}</legend>
          <label>{t('videoPublish.youtubeTitle')}<input maxLength={100} value={optionFor(destination).title} onChange={(event) => changeOption(destination, 'title', event.target.value)} required /></label>
          <label>{t('videoPublish.visibility')}<select aria-label={t('videoPublish.visibility')} value={optionFor(destination).visibility} onChange={(event) => changeOption(destination, 'visibility', event.target.value)}>{['private', 'unlisted', 'public'].map((value) => <option key={value} value={value}>{t(`videoPublish.visibility.${value}`)}</option>)}</select></label>
          <label>{t('videoPublish.audience')}<select aria-label={t('videoPublish.audience')} value={optionFor(destination).made_for_kids === null ? '' : String(optionFor(destination).made_for_kids)} onChange={(event) => changeOption(destination, 'made_for_kids', event.target.value === '' ? null : event.target.value === 'true')} required>
            <option value="">{t('videoPublish.chooseAudience')}</option><option value="false">{t('videoPublish.generalAudience')}</option><option value="true">{t('videoPublish.kidsAudience')}</option>
          </select></label>
          <label><input type="checkbox" checked={optionFor(destination).contains_synthetic_media} onChange={(event) => changeOption(destination, 'contains_synthetic_media', event.target.checked)} />{t('videoPublish.synthetic')}</label>
          <p className="muted">{t('videoPublish.youtubeHint')}</p>
        </fieldset>)}
        {timeFields}
        {dirty && <p className="muted">{t('videoPublish.dirty')}</p>}
        <button type="button" className="btn" disabled={busy || dirty || !selected.length || !timingValid || !optionsValid} onClick={approve}>{busy ? t('postEditor.publishing') : t(scheduled ? 'videoPublish.schedule' : 'videoPublish.approve')}</button>
      </>}
    </>}
  </section>
}
