// Neutral presentation metadata. This map never grants capabilities or selects a channel.
const base = import.meta.env.BASE_URL
export const platformArtwork = Object.freeze(Object.fromEntries([
  'facebook', 'instagram', 'tiktok', 'youtube', 'telegram', 'vk', 'max',
  'linkedin', 'x', 'bluesky', 'mastodon', 'rss', 'zen', 'postbridge',
].map(id => [id, Object.freeze({ file: id === 'postbridge' ? 'postbridge-mark.svg' : `assets/platforms/${id}.${id === 'linkedin' ? 'png' : 'svg'}` })])))

export function platformArtworkId(value) {
  const id = typeof value === 'string' ? value.trim().toLowerCase() : ''
  return id === 'twitter' ? 'x' : id
}

export function platformLogo(value) {
  const id = platformArtworkId(value)
  const artwork = Object.hasOwn(platformArtwork, id) ? platformArtwork[id] : null
  return artwork ? { id, src: `${base}${artwork.file}` } : null
}
