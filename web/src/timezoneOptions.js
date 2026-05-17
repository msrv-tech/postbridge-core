/**
 * Curated IANA timezone options with localized city labels and current UTC offsets.
 */

const MSK_IANA = 'Europe/Moscow'

const RUSSIAN_TIMEZONES = [
  {
    value: 'Europe/Kaliningrad',
    cityKey: 'timezone.city.kaliningrad',
  },
  {
    value: 'Europe/Moscow',
    cityKey: 'timezone.city.moscow',
  },
  {
    value: 'Europe/Samara',
    cityKey: 'timezone.city.samara',
  },
  {
    value: 'Asia/Yekaterinburg',
    cityKey: 'timezone.city.yekaterinburg',
  },
  {
    value: 'Asia/Omsk',
    cityKey: 'timezone.city.omsk',
  },
  {
    value: 'Asia/Novosibirsk',
    cityKey: 'timezone.city.novosibirsk',
  },
  {
    value: 'Asia/Irkutsk',
    cityKey: 'timezone.city.irkutsk',
  },
  {
    value: 'Asia/Yakutsk',
    cityKey: 'timezone.city.yakutsk',
  },
  {
    value: 'Asia/Vladivostok',
    cityKey: 'timezone.city.vladivostok',
  },
  {
    value: 'Asia/Magadan',
    cityKey: 'timezone.city.magadan',
  },
  {
    value: 'Asia/Kamchatka',
    cityKey: 'timezone.city.kamchatka',
  },
]

export const RUSSIAN_TIMEZONE_VALUES = new Set(RUSSIAN_TIMEZONES.map((r) => r.value))

/** @param {string} offsetPart e.g. GMT+3, UTC+03:00 */
function parseLongOffsetToMinutes(offsetPart) {
  if (!offsetPart || typeof offsetPart !== 'string') return null
  const m = offsetPart
    .trim()
    .match(/^(?:GMT|UTC)([+-])(\d{1,2})(?::(\d{2}))?$/i)
  if (!m) return null
  const sign = m[1] === '-' ? -1 : 1
  const h = parseInt(m[2], 10)
  const min = m[3] != null ? parseInt(m[3], 10) : 0
  return sign * (h * 60 + min)
}

function timezoneOffsetMinutesFromUtc(iana, at = new Date()) {
  for (const timeZoneName of ['longOffset', 'shortOffset']) {
    try {
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: iana,
        timeZoneName,
      }).formatToParts(at)
      const raw = parts.find((p) => p.type === 'timeZoneName')?.value
      const parsed = parseLongOffsetToMinutes(raw)
      if (parsed != null) return parsed
    } catch {
      /* ignore */
    }
  }
  return null
}

function minutesRelativeToMsk(iana, at = new Date()) {
  const msk = timezoneOffsetMinutesFromUtc(MSK_IANA, at)
  const loc = timezoneOffsetMinutesFromUtc(iana, at)
  if (msk == null || loc == null) return null
  return loc - msk
}

function formatMskCompact(diffMinutes, t) {
  const label = t('timezone.msk')
  if (diffMinutes == null) return `${label} ?`
  if (diffMinutes === 0) return `${label}+0`
  const sign = diffMinutes > 0 ? '+' : '−'
  const abs = Math.abs(diffMinutes)
  const h = Math.floor(abs / 60)
  const m = abs % 60
  if (m === 0) return `${label}${sign}${h}`
  return `${label}${sign}${h}:${String(m).padStart(2, '0')}`
}

function formatUtcOffsetMinutes(minutes) {
  if (minutes == null) return 'UTC ?'
  const sign = minutes >= 0 ? '+' : '−'
  const abs = Math.abs(minutes)
  const h = Math.floor(abs / 60)
  const m = abs % 60
  if (m) return `UTC${sign}${h}:${String(m).padStart(2, '0')}`
  return `UTC${sign}${h}`
}

function buildTimezoneLabel(value, cityLine, at, t) {
  const utcMin = timezoneOffsetMinutesFromUtc(value, at)
  const relMin = minutesRelativeToMsk(value, at)
  const utcStr = formatUtcOffsetMinutes(utcMin)
  const mskStr = formatMskCompact(relMin, t)
  return `${cityLine} — ${utcStr}, ${mskStr}`
}

/**
 * @returns {{ value: string, label: string }[]}
 */
export function getTimezoneSelectOptions(t) {
  const ref = new Date()
  const rows = RUSSIAN_TIMEZONES.map(({ value, cityKey }) => ({
    value,
    label: buildTimezoneLabel(value, t(cityKey), ref, t),
    offsetUtc: timezoneOffsetMinutesFromUtc(value, ref) ?? 0,
  }))
  rows.sort((a, b) => a.offsetUtc - b.offsetUtc)
  return rows.map(({ value, label }) => ({ value, label }))
}
