/** Resolve a wall-clock minute without guessing across daylight-saving gaps or folds. */
export function resolveDeliveryTime(value, timezone) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value || '')
  if (!match) return { status: 'invalid' }
  const [year, month, day, hour, minute] = match.slice(1).map(Number)
  const wall = Date.UTC(year, month - 1, day, hour, minute)
  if (new Date(wall).toISOString().slice(0, 16) !== value) return { status: 'invalid' }
  try {
    const formatter = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
    const parts = (instant) => Object.fromEntries(formatter.formatToParts(new Date(instant)).map((part) => [part.type, part.value]))
    const localMillis = (instant) => {
      const p = parts(instant)
      return Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), Number(p.hour), Number(p.minute), Number(p.second))
    }
    const offsets = new Set()
    for (let hours = -48; hours <= 48; hours += 12) {
      const sample = wall + hours * 3600000
      offsets.add(localMillis(sample) - sample)
    }
    const matches = [...offsets].map((offset) => wall - offset).filter((instant) => localMillis(instant) === wall)
    return matches.length === 1 ? { status: 'valid', iso: new Date(matches[0]).toISOString() }
      : { status: matches.length ? 'ambiguous' : 'missing' }
  } catch { return { status: 'invalid' } }
}

export function deliveryLocalInput(iso, timezone) {
  if (!iso) return ''
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: timezone, year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(iso)).map((part) => [part.type, part.value]))
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
  } catch { return '' }
}

export function formatDeliveryTime(iso, timezone, locale) {
  return new Intl.DateTimeFormat(locale === 'ru' ? 'ru-RU' : 'en-GB', {
    timeZone: timezone || 'UTC', dateStyle: 'medium', timeStyle: 'short',
  }).format(new Date(iso))
}
