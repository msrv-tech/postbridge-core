import { useI18n } from '../i18n'

export const emptyTikTokOptions = () => ({ privacy_level: '', allow_comment: false, allow_duet: false,
  allow_stitch: false, commercial_content: false, brand_organic: false, branded_content: false,
  music_usage_confirmed: false, original_content_confirmed: false })

export function validTikTokOptions(value, creator, duration, text) {
  return Boolean(value && creator && creator.privacy_level_options?.includes(value.privacy_level)
    && duration > 0 && duration <= creator.max_video_post_duration_sec && text.length <= 2200
    && value.music_usage_confirmed && value.original_content_confirmed
    && value.commercial_content === Boolean(value.brand_organic || value.branded_content)
    && !(value.branded_content && value.privacy_level === 'SELF_ONLY')
    && !['comment', 'duet', 'stitch'].some(key => value[`allow_${key}`] && creator[`${key}_disabled`]))
}

export default function TikTokPublicationOptions({ state, value, onChange, onRefresh, duration, busy }) {
  const { t } = useI18n()
  const change = (key, next) => onChange({ ...value, [key]: next,
    ...(key === 'commercial_content' && !next ? { brand_organic: false, branded_content: false } : {}),
    ...(['branded_content', 'brand_organic'].includes(key) ? { music_usage_confirmed: false } : {}) })
  return <fieldset className="video-provider-options" disabled={busy}>
    <legend>TikTok</legend>
    {!state || state.loading ? <p>{t('common.loading')}</p> : state.error ? <p className="error" role="alert">{state.error}</p> : <>
      <p><strong>{state.creator.creator_nickname}</strong>{' · '}@{state.creator.creator_username}</p>
      {state.creator.app_audited === false && <p className="muted">{t('tiktok.auditPending')}</p>}
      <label>{t('videoPublish.visibility')}<select aria-label={t('videoPublish.visibility')} value={value.privacy_level} onChange={event => change('privacy_level', event.target.value)}>
        <option value="">{t('tiktok.chooseVisibility')}</option>
        {state.creator.privacy_level_options.map(key => <option value={key} key={key} disabled={key === 'SELF_ONLY' && value.branded_content}>{t(`tiktok.privacy.${key}`)}</option>)}
      </select></label>
      <p className="muted">{t('tiktok.durationLimit', { seconds: state.creator.max_video_post_duration_sec })}</p>
      {duration > state.creator.max_video_post_duration_sec && <p className="error">{t('tiktok.tooLong')}</p>}
      {['comment', 'duet', 'stitch'].map(key => <label key={key}>
        <input type="checkbox" checked={value[`allow_${key}`]} disabled={Boolean(state.creator[`${key}_disabled`])}
          onChange={event => change(`allow_${key}`, event.target.checked)} />{t(`tiktok.allow.${key}`)}
        {state.creator[`${key}_disabled`] && <span className="muted">{' · '}{t('tiktok.disabledInAccount')}</span>}
      </label>)}
      <label><input type="checkbox" checked={value.commercial_content} onChange={event => change('commercial_content', event.target.checked)} />{t('tiktok.commercial')}</label>
      {value.commercial_content && <>
        <label><input type="checkbox" checked={value.brand_organic} onChange={event => change('brand_organic', event.target.checked)} />{t('tiktok.ownBrand')}</label>
        <label><input type="checkbox" checked={value.branded_content} disabled={value.privacy_level === 'SELF_ONLY'} onChange={event => change('branded_content', event.target.checked)} />{t('tiktok.branded')}</label>
        {value.privacy_level === 'SELF_ONLY' && <p className="muted">{t('tiktok.brandedPrivate')}</p>}
        {(value.brand_organic || value.branded_content) ? <p>{t(value.branded_content ? 'tiktok.paidLabel' : 'tiktok.promotionLabel')}</p> : <p className="error">{t('tiktok.chooseCommercial')}</p>}
      </>}
      <label><input type="checkbox" checked={value.original_content_confirmed} onChange={event => change('original_content_confirmed', event.target.checked)} />{t('tiktok.original')}</label>
      <label><input type="checkbox" checked={value.music_usage_confirmed} onChange={event => change('music_usage_confirmed', event.target.checked)} />
        <span>{t('tiktok.byPosting')}{' '}{value.branded_content && <><a href="https://www.tiktok.com/legal/page/global/bc-policy/en" target="_blank" rel="noreferrer">{t('tiktok.brandedPolicy')}</a>{' '}{t('tiktok.and')}{' '}</>}
          <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noreferrer">{t('tiktok.musicConfirmation')}</a>.</span>
      </label>
      <p className="muted">{t('tiktok.processingHint')}</p>
    </>}
    <button type="button" className="btn btn-secondary btn-small" disabled={busy || state?.loading} onClick={onRefresh}>{t('tiktok.refreshAccount')}</button>
  </fieldset>
}
