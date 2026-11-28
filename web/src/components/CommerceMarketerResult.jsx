import { Link } from 'react-router-dom'
import { safeConnectorHref } from '../adapters/connectorPolicy'
import { useI18n } from '../i18n'
import PlatformLogo from './PlatformLogo'

const publicationOperations = new Set(['prepare_publication', 'schedule_post', 'reschedule_post', 'retry_failed_publication'])
const objectOperations = new Set([...publicationOperations, 'create_draft', 'update_draft', 'cancel_scheduled_post'])
const cardKinds = new Set(['content_generation', 'video_generation', 'post_draft', 'schedule', 'schedule_edit', 'video_delivery', 'postbridge_action'])
const videoDeliveryActions = new Set(['approve', 'reschedule', 'cancel', 'retry', 'reconcile'])
const nonempty = value => typeof value === 'string' && value.trim() !== ''
const cardId = value => /^[1-9][0-9]{0,17}$/.test(String(value || ''))

function safeWorkspaceResult(workspaceId, response) {
  const prefix = `/workspaces/${encodeURIComponent(workspaceId)}/`
  const postId = response.post_reference?.id || response.content_result?.post_id
  const path = safeConnectorHref(response.route)
  if (nonempty(postId) && !['.', '..'].includes(postId)) {
    const editor = `${prefix}content/${encodeURIComponent(postId)}`
    return !response.route || path === editor ? { path: editor, post: true } : null
  }
  if (!path?.startsWith(prefix)) return null
  const remainder = path.slice(prefix.length)
  if (!/^(?:content|agents)\//.test(remainder)) return null
  return { path, post: /^content\/[^/]+$/.test(remainder) }
}

function mediaPath(value) {
  const path = safeConnectorHref(value)
  return path && /^\/(?:core\/|api\/app\/)?media\//.test(path) ? path : null
}

function Materials({ items }) {
  if (!Array.isArray(items)) return null
  return <div className="commerce-marketer-materials">{items.map((item, index) => {
    const path = mediaPath(item?.url)
    if (!path) return null
    return <figure key={`${path}-${index}`}>
      <img src={path} alt={item.name || ''} loading="lazy" />
      {item.name && <figcaption>{item.name}</figcaption>}
    </figure>
  })}</div>
}

export function CommerceResult({ workspaceId, binding, response }) {
  const { t } = useI18n()
  const content = response?.content_result
  const target = safeWorkspaceResult(workspaceId, response)
  if (!content && !target) return null
  const state = content?.persisted === false ? 'notSaved' : content?.persisted === true
    ? content.status === 'published' ? 'sent' : content.scheduled_publish_at ? 'scheduled'
      : content.operation === 'update' || content.operation === 'update_draft' ? 'updated' : 'saved'
    : null
  return <section className="commerce-marketer-result" aria-label={t('commerceMarketer.result')}>
    {state && <strong>{t(`commerceMarketer.result.${state}`)}</strong>}
    {nonempty(content?.text) && <p className="commerce-marketer-text">{content.text}</p>}
    {target && content?.persisted !== false && <Link className="btn btn-secondary btn-small" to={target.path}
      state={target.post ? { commerceMarketer: { provider: binding.provider, connectionId: binding.id } } : undefined}>
      {t(target.post ? 'commerceMarketer.editor' : 'commerceMarketer.openResult')}
    </Link>}
  </section>
}

export function CommerceVideoPreview({ result, disabled, onRead }) {
  const { t } = useI18n()
  const path = result?.status === 'succeeded' && mediaPath(result.output_url)
  const key = result?.status === 'succeeded' ? (path ? 'ready' : 'unavailable')
    : ['failed', 'canceled', 'cancelled'].includes(result?.status) ? 'failed'
      : result?.status === 'not_started' ? 'notStarted' : 'pending'
  return <div className="commerce-marketer-result">
    <button type="button" className="btn btn-secondary btn-small" disabled={disabled} onClick={onRead}>
      {t('commerceMarketer.video.check')}
    </button>
    {result && <p role="status">{t(`commerceMarketer.video.${key}`)}</p>}
    {path && <video className="commerce-marketer-video" controls playsInline preload="metadata"
      aria-label={t('commerceMarketer.video.ready')} src={path} />}
  </div>
}

function ConfirmationDetails({ summary }) {
  const { t } = useI18n()
  const destinations = Array.isArray(summary.destinations) ? summary.destinations : []
  const text = [summary.text, summary.caption, summary.brief].find(nonempty)
  const time = [summary.scheduled_publish_at, summary.publish_at, summary.schedule_cron, summary.time].filter(nonempty)
  const video = mediaPath(summary.output_url)
  const scenes = Array.isArray(summary.scenes) ? summary.scenes.filter(scene => scene && typeof scene === 'object') : []
  return <>
    {nonempty(summary.title) && <p><strong>{summary.title}</strong></p>}
    {text && <p className="commerce-marketer-text">{text}</p>}
    {destinations.length > 0 && <>
      <p>{t('commerceMarketer.destinations')}</p>
      <ul>{destinations.filter(destination => nonempty(destination?.title) || nonempty(destination?.platform)).map((destination, index) => <li key={destination.bridge_id || index}>
        {nonempty(destination.platform) && <PlatformLogo platform={destination.platform} compact />}
        {destination.title || destination.platform}
      </li>)}</ul>
    </>}
    {time.length > 0 && <p>{t('commerceMarketer.timestamp', { value: [...time, summary.timezone].filter(nonempty).join(' · ') })}</p>}
    <Materials items={summary.materials} />
    {video && <video className="commerce-marketer-video" controls playsInline preload="metadata"
      aria-label={t('videoPublish.preview')} src={video} />}
    {scenes.length > 0 && <details>
      <summary>{t('assistant.video.scenes')}</summary>
      <ol>{scenes.map((scene, index) => <li key={index}>
        {[scene.caption, scene.narration_text, scene.image_prompt].filter(nonempty).join(' · ')}
      </li>)}</ol>
    </details>}
  </>
}

export function CommerceActionCard({ card, disabled, uncertain, onApply }) {
  const { t } = useI18n()
  if (!cardId(card?.id)) return null
  const summary = card.summary || {}
  const object = card.kind === 'postbridge_action'
  const videoDelivery = card.kind === 'video_delivery'
  const supported = cardKinds.has(card.kind) && (!object || objectOperations.has(summary.operation))
    && (!videoDelivery || videoDeliveryActions.has(summary.action))
  const publishing = (object && publicationOperations.has(summary.operation)) || videoDelivery
  const scheduling = ['schedule', 'schedule_edit'].includes(card.kind) || ['schedule_post', 'reschedule_post'].includes(summary.operation)
  const time = summary.scheduled_publish_at || summary.schedule_cron || summary.time || summary.publish_at
  const complete = (!publishing || (Array.isArray(summary.destinations) && summary.destinations.some(item => item?.title || item?.platform)))
    && (!scheduling || nonempty(time)) && (!(object && publicationOperations.has(summary.operation)) || summary.requires_confirmation === true)
  const locked = uncertain || Boolean(card.execution_status)
  const applied = card.status === 'applied'
  const label = object && supported ? t(`commerceMarketer.operation.${summary.operation}`)
    : supported ? t(`videoAssistant.card.${card.kind}`) : t('commerceMarketer.review')
  const confirmation = videoDelivery && supported ? `videoAssistant.delivery.${summary.action}`
    : scheduling ? 'commerceMarketer.confirmSchedule' : publishing ? 'commerceMarketer.confirmDelivery' : 'commerceMarketer.confirmAction'
  return <section className="commerce-marketer-result" aria-label={label}>
    <strong>{label}</strong>
    <ConfirmationDetails summary={summary} />
    {applied ? <p role="status">{t('videoAssistant.applied')}</p> : locked
      ? <p>{t('commerceMarketer.actionUnknown')}</p> : supported && complete && card.status === 'proposed' && onApply
        ? <button type="button" className="btn btn-small" disabled={disabled} onClick={() => onApply(card)}>
          {t(confirmation)}
        </button> : <p>{t('commerceMarketer.reviewInEditor')}</p>}
  </section>
}
