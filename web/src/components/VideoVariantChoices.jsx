import VoicePreview from './VoicePreview'
import { useI18n } from '../i18n'

export default function VideoVariantChoices({ value, onChange, disabled }) {
  const { t } = useI18n()
  const change = (index, key, item) => onChange(value.map((v, i) => i === index ? { ...v, [key]: item } : v))
  return <div className="video-variants">
    <p className="muted">{t('videoBatch.reuse')}</p>
    {value.map((v, index) => <fieldset key={index} disabled={disabled}>
      <legend>{t('videoBatch.variant', { number: index + 1 })}</legend>
      <label>{t('videoVoice.language')}<select value={v.language} onChange={(e) => change(index, 'language', e.target.value)}>{['ru', 'en'].map((l) => <option value={l} key={l}>{t(`videoVoice.language.${l}`)}</option>)}</select></label>
      <label>{t('videoVoice.voice')}<select value={v.voice} onChange={(e) => change(index, 'voice', e.target.value)}>{['Kore', 'Charon', 'Puck'].map((voice) => <option value={voice} key={voice}>{t(`videoVoice.voice.${voice}`)}</option>)}</select></label><VoicePreview language={v.language} voice={v.voice} />
      <label>{t('videoVoice.style')}<select value={v.voice_style} onChange={(e) => change(index, 'voice_style', e.target.value)}>{['neutral', 'warm', 'energetic'].map((s) => <option value={s} key={s}>{t(`videoVoice.style.${s}`)}</option>)}</select></label>
      <button type="button" className="btn btn-secondary btn-small" onClick={() => onChange(value.filter((_, i) => i !== index))}>{t('common.delete')}</button>
    </fieldset>)}
    {value.length < 6 && <button type="button" disabled={disabled} className="btn btn-secondary btn-small" onClick={() => {
      const next = ['en', 'ru'].flatMap((language) => ['Kore', 'Charon', 'Puck'].map((voice) => ({ language, voice, voice_style: 'neutral' })))
        .find((candidate) => !value.some((v) => JSON.stringify(v) === JSON.stringify(candidate)))
      if (next) onChange([...value, next])
    }}>{t('videoBatch.addVariant')}</button>}
  </div>
}
