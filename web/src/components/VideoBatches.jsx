import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useI18n } from '../i18n'
import { listVideoBatches, cancelVideoBatch } from '../adapters/video'

const active = (b) => ['queued', 'running'].includes(b.status)

export default function VideoBatches({ workspaceId, projectId, refreshKey = 0 }) {
  const { t } = useI18n()
  const [batches, setBatches] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(null)
  useEffect(() => {
    let current = true, inFlight = false, timer, previousSignature
    setBatches([]); setError('')
    const refresh = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const result = await listVideoBatches(workspaceId, projectId)
        if (current) {
          setBatches(result.items || []); setError('')
          const signature = JSON.stringify(result.items)
          if (previousSignature !== signature) window.dispatchEvent(new CustomEvent('postbridge-video-updated', {
            detail: { workspaceId, project_id: projectId },
          }))
          previousSignature = signature
          if ((result.items || []).some(active)) timer = setTimeout(refresh, 5000)
        }
      } catch (e) { if (current) { setError(e.message); timer = setTimeout(refresh, 10000) } }
      finally { inFlight = false }
    }
    refresh()
    return () => { current = false; clearTimeout(timer) }
  }, [workspaceId, projectId, refreshKey])
  if (!batches.length && !error) return null
  return <section className="card video-batches">
    <h3>{t('videoBatch.title')}</h3>
    {error && <p role="alert" className="error">{error}</p>}
    {batches.map((batch) => <details key={batch.id} open={active(batch)}>
      <summary>{t(batch.kind === 'voices' ? 'videoBatch.voices' : 'videoBatch.originals')} · {t(`video.status.${batch.status}`)} · {batch.items.filter((i) => i.status === 'succeeded').length}/{batch.items.length}</summary>
      {batch.error_code && <p className="error">{t(`api.error.${batch.error_code}`, { defaultValue: t('videoBatch.stopped') })}</p>}
      <ul>{batch.items.map((item) => <li key={item.id}>
        {t('videoBatch.variant', { number: item.position + 1 })}{item.payload.language && ` · ${t(`videoVoice.language.${item.payload.language}`)}`} · {t(`video.status.${item.status}`)}{' '}
        {item.project_id && <Link to={`/workspaces/${workspaceId}/content/videos/${item.project_id}${item.version ? `?version=${item.version}` : ''}`}>{t('video.open')}</Link>}{' '}
        {item.output_url && <a href={`${item.output_url}?download=1`} target="_blank" rel="noreferrer">{t('video.download')}</a>}
        {item.usage?.uncertain && <p className="muted">{t('video.reconciliationHint')}</p>}
      </li>)}</ul>
      {active(batch) && <button className="btn btn-secondary btn-small" disabled={busy === batch.id} onClick={async () => {
        setBusy(batch.id)
        try { const result = await cancelVideoBatch(workspaceId, batch.id); setBatches((all) => all.map((b) => b.id === result.id ? result : b)) }
        catch (e) { setError(e.message) } finally { setBusy(null) }
      }}>{t('videoBatch.cancel')}</button>}
    </details>)}
  </section>
}
