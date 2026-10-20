import { useId } from 'react'
import { useI18n } from '../i18n'

export const defaultVideoBridgeSettings = (platform) => platform === 'tiktok' ? { format: 'tiktok', contains_synthetic_media: true } : ({ format: { youtube: 'short', instagram: 'reel', telegram: 'video' }[platform], visibility: 'private', made_for_kids: null, contains_synthetic_media: true })

export default function VideoBridgeSettings({ platform, value, onChange, busy = false }) {
  const { t } = useI18n()
  const id = useId()
  const settings = value || defaultVideoBridgeSettings(platform)
  const change = (key, next) => onChange({ ...settings, [key]: next })
  return <fieldset className="video-provider-options" disabled={busy}>
    <legend>{t('videoBridge.receivingSettings')}</legend>
    <p>{t('videoBridge.format', { format: platform === 'youtube' ? 'Shorts' : platform === 'instagram' ? 'Reels' : t('videoPublish.format.video') })}</p>
    {platform === 'youtube' && <>
      <p className="muted">{t('videoBridge.titleFromPost')}</p>
      <label htmlFor={`${id}-visibility`}>{t('videoPublish.visibility')}</label>
      <select id={`${id}-visibility`} value={settings.visibility} onChange={event => change('visibility', event.target.value)}>{['private', 'unlisted', 'public'].map(key => <option key={key} value={key}>{t(`videoPublish.visibility.${key}`)}</option>)}</select>
      <label htmlFor={`${id}-audience`}>{t('videoPublish.audience')}</label>
      <select id={`${id}-audience`} value={settings.made_for_kids == null ? '' : String(settings.made_for_kids)} onChange={event => change('made_for_kids', event.target.value === '' ? null : event.target.value === 'true')}>
        <option value="">{t('videoPublish.chooseAudience')}</option><option value="false">{t('videoPublish.generalAudience')}</option><option value="true">{t('videoPublish.kidsAudience')}</option>
      </select>
      <label><input type="checkbox" checked={settings.contains_synthetic_media} onChange={event => change('contains_synthetic_media', event.target.checked)} />{t('videoPublish.synthetic')}</label>
    </>}
    {platform === 'tiktok' && <>
      <p className="muted">{t('tiktok.perPostChoices')}</p>
      <label><input type="checkbox" checked={settings.contains_synthetic_media} onChange={event => change('contains_synthetic_media', event.target.checked)} />{t('videoPublish.synthetic')}</label>
    </>}
    <p className="muted">{t('videoBridge.confirmationHint')}</p>
  </fieldset>
}
