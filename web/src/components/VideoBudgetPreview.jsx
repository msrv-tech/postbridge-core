import { useI18n } from '../i18n'

export { estimateVideoUnits } from '../videoBudget'

export default function VideoBudgetPreview({ units, limit }) {
  const { t } = useI18n()
  if (!units) return null
  return <div className="video-budget-preview">
    <p>{t('assistant.reserve', { units: limit ? Math.min(units, Number(limit)) : units })}</p>
    <p className="muted">{t('assistant.reserveHint')}</p>
  </div>
}
