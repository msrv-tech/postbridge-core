/**
 * Curated IANA timezone options with localized city labels and current UTC offsets.
 */

const MSK_IANA = 'Europe/Moscow'

/**
 * Канонический IANA + подпись с городами для UI (одна строка на пояс).
 * Где несколько городов в одном поясе — перечислены через запятую.
 */
const RUSSIAN_TIMEZONES = [
  {
    value: 'Europe/Kaliningrad',
    city: { ru: 'Калининград', en: 'Kaliningrad' },
  },
  {
    value: 'Europe/Moscow',
    city: { ru: 'Москва, Санкт-Петербург и большинство регионов', en: 'Moscow, Saint Petersburg, and most regions' },
  },
  {
    value: 'Europe/Samara',
    city: { ru: 'Самара, Саратов, Ульяновск, Астрахань', en: 'Samara, Saratov, Ulyanovsk, Astrakhan' },
  },
  {
    value: 'Asia/Yekaterinburg',
    city: { ru: 'Екатеринбург, Пермь', en: 'Yekaterinburg, Perm' },
  },
  {
    value: 'Asia/Omsk',
    city: { ru: 'Омск', en: 'Omsk' },
  },
  {
    value: 'Asia/Novosibirsk',
    city: { ru: 'Новосибирск, Красноярск, Томск, Барнаул', en: 'Novosibirsk, Krasnoyarsk, Tomsk, Barnaul' },
  },
  {
    value: 'Asia/Irkutsk',
    city: { ru: 'Иркутск', en: 'Irkutsk' },
  },
  {
    value: 'Asia/Yakutsk',
    city: { ru: 'Якутск, Чита', en: 'Yakutsk, Chita' },
  },
  {
    value: 'Asia/Vladivostok',
    city: { ru: 'Владивосток', en: 'Vladivostok' },
  },
  {
    value: 'Asia/Magadan',
    city: { ru: 'Магадан, Сахалин', en: 'Magadan, Sakhalin' },
  },
  {
    value: 'Asia/Kamchatka',
    city: { ru: 'Петропавловск-Камчатский, Анадырь', en: 'Petropavlovsk-Kamchatsky, Anadyr' },
  },
]

/** Все IANA из списка выше (для проверки «сохранённого ранее» значения). */
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

/** Компактно: МСК+0, МСК+1, МСК−1 (смещение в часах от московского времени). */
function formatMskCompact(diffMinutes, locale = 'ru') {
  const label = String(locale || 'ru').toLowerCase().startsWith('en') ? 'MSK' : 'МСК'
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

function buildTimezoneLabel(value, cityLine, at, locale = 'ru') {
  const utcMin = timezoneOffsetMinutesFromUtc(value, at)
  const relMin = minutesRelativeToMsk(value, at)
  const utcStr = formatUtcOffsetMinutes(utcMin)
  const mskStr = formatMskCompact(relMin, locale)
  return `${cityLine} — ${utcStr}, ${mskStr}`
}

/**
 * @returns {{ value: string, label: string }[]}
 */
export function getTimezoneSelectOptions(locale = 'ru') {
  const ref = new Date()
  const rows = RUSSIAN_TIMEZONES.map(({ value, city }) => ({
    value,
    label: buildTimezoneLabel(value, city[String(locale || 'ru').startsWith('en') ? 'en' : 'ru'], ref, locale),
    offsetUtc: timezoneOffsetMinutesFromUtc(value, ref) ?? 0,
  }))
  rows.sort((a, b) => a.offsetUtc - b.offsetUtc)
  return rows.map(({ value, label }) => ({ value, label }))
}
