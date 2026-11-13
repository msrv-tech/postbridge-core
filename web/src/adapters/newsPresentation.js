const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif'])
const VIDEO_TYPES = new Set(['video/mp4', 'video/webm', 'video/quicktime'])

export function newsText(value) {
  return typeof value === 'string' ? value : ''
}

export function safeNewsHttpsUrl(value) {
  if (typeof value !== 'string' || value.length > 8192 || !/^https:\/\/[^/]/i.test(value)
    || /[\s\u0000-\u001f\u007f\\<>"']/.test(value)) return null
  const authority = value.slice(8).split(/[/?#]/, 1)[0]
  if (authority.includes('@')) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.hostname && !url.username && !url.password ? value : null
  } catch { return null }
}

export function newsDestination(item) {
  const slug = newsText(item?.slug)
  if (/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,199}$/.test(slug)) return { to: `/news/${slug}` }
  const href = safeNewsHttpsUrl(item?.link)
  return href ? { href } : null
}

export function newsMedia(item) {
  const url = safeNewsHttpsUrl(item?.media_url)
  if (!url) return null
  return { url, kind: IMAGE_TYPES.has(item.media_type) ? 'image' : VIDEO_TYPES.has(item.media_type) ? 'video' : 'file' }
}

function decodeText(value) {
  const named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }
  return value.replace(/&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);/gi, entity => {
    const name = entity.slice(1, -1)
    if (name[0] !== '#') return named[name.toLowerCase()]
    const point = /^#x/i.test(name) ? parseInt(name.slice(2), 16) : Number(name.slice(1))
    return point > 0 && point <= 0x10ffff && !(point >= 0xd800 && point <= 0xdfff) ? String.fromCodePoint(point) : '\ufffd'
  })
}

// Parse only the backend's small serialized grammar. Never create an HTML DOM:
// unsupported/malformed markup falls back to content_text, without loading resources.
export function newsRichParts(value) {
  if (typeof value !== 'string' || !value || value.length > 100000) return null
  const parts = []
  let cursor = 0
  while (cursor < value.length) {
    const rest = value.slice(cursor)
    const br = /^<br\s*\/?>/.exec(rest)
    if (br) {
      parts.push({ kind: 'break' })
      cursor += br[0].length
    } else if (rest[0] === '<') {
      const anchor = /^<a href="([^"<>]*)" rel="noopener noreferrer">([^<]*)<\/a>/.exec(rest)
      if (!anchor) return null
      const text = decodeText(anchor[2]), href = safeNewsHttpsUrl(decodeText(anchor[1]))
      parts.push(href ? { kind: 'link', text, href } : { kind: 'text', text })
      cursor += anchor[0].length
    } else {
      const next = value.indexOf('<', cursor)
      const end = next === -1 ? value.length : next
      parts.push({ kind: 'text', text: decodeText(value.slice(cursor, end)) })
      cursor = end
    }
  }
  return parts
}
