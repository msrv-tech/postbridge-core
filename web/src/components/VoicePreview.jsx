import { useI18n } from '../i18n'

export default function VoicePreview({ language, voice }) {
  const { t } = useI18n()
  if (!['ru', 'en'].includes(language) || !['Kore', 'Charon', 'Puck'].includes(voice)) return null
  return <details className="voice-preview" key={`${language}-${voice}`}>
    <summary>{t('videoVoice.listenSample')}</summary>
    <audio controls preload="none" src={`${import.meta.env.BASE_URL}voice-samples/${language}-${voice}.mp3`} aria-label={t('videoVoice.listenSample')} />
    <small className="muted">{t('videoVoice.sampleHint')}</small>
  </details>
}
