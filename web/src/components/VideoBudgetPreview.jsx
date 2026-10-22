import { useI18n } from '../i18n'

export function estimateVideoUnits(caps, video, count = 1, variants = []) {
  const generated = video.scenes?.some((s) => !s.asset_id || s.regenerate_image || s.regenerate_video)
  const paid = video.mode === 'autopilot' || video.narration_enabled || generated
  let scenes = video.scenes?.filter((s) => s.regenerate_video || !s.asset_id && s.generation_kind === 'video').length || 0
  if (video.mode === 'autopilot' && video.generated_visuals === 'video') scenes = Math.max(scenes, Math.ceil((video.duration_seconds || 20) / 8))
  const original = paid ? Math.min(100000000, (caps?.reservation_units || 0) + scenes * (caps?.video_scene_reserve_units || 0)) : 0
  return count * (original + variants.length * (caps?.reservation_units || 0))
}

export default function VideoBudgetPreview({ units, limit }) {
  const { t } = useI18n()
  if (!units) return null
  return <div className="video-budget-preview">
    <p>{t('assistant.reserve', { units: limit ? Math.min(units, Number(limit)) : units })}</p>
    <p className="muted">{t('assistant.reserveHint')}</p>
  </div>
}
