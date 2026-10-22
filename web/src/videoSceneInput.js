export const videoSceneInput = (scene) => ({
  asset_id: scene.asset_id,
  duration_seconds: Number(scene.duration_seconds),
  caption: scene.caption || '',
  ...Object.fromEntries(['scene_id', 'purpose', 'visual_description', 'image_prompt', 'generation_kind', 'source_start_seconds', 'motion', 'transition']
    .filter((key) => scene[key] != null && scene[key] !== '').map((key) => [key, key === 'source_start_seconds' ? Number(scene[key]) : scene[key]])),
  ...(scene.regenerate_image ? { regenerate_image: true } : {}),
  ...(scene.regenerate_video ? { regenerate_video: true } : {}),
  ...(scene.narration_text != null ? { narration_text: scene.narration_text } : {}),
  ...(scene.speech_asset_id ? { speech_asset_id: scene.speech_asset_id } : {}),
})
