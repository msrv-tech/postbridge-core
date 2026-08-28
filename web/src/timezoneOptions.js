/**
 * Curated IANA timezone options with localized regional labels and current UTC offsets.
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

const INTERNATIONAL_TIMEZONES = [
  { value: 'Etc/GMT+12', labelKey: 'timezone.region.datelineWest' },
  { value: 'Pacific/Pago_Pago', labelKey: 'timezone.region.samoa' },
  { value: 'Pacific/Honolulu', labelKey: 'timezone.region.hawaiiAleutian' },
  { value: 'America/Anchorage', labelKey: 'timezone.region.alaska' },
  { value: 'America/Los_Angeles', labelKey: 'timezone.region.pacificNorthAmerica' },
  { value: 'America/Denver', labelKey: 'timezone.region.mountainNorthAmerica' },
  { value: 'America/Chicago', labelKey: 'timezone.region.centralNorthAmerica' },
  { value: 'America/New_York', labelKey: 'timezone.region.easternNorthAmerica' },
  { value: 'America/Halifax', labelKey: 'timezone.region.atlanticNorthAmerica' },
  { value: 'America/St_Johns', labelKey: 'timezone.region.newfoundland' },
  { value: 'America/Sao_Paulo', labelKey: 'timezone.region.brasilia' },
  { value: 'Atlantic/Azores', labelKey: 'timezone.region.azores' },
  { value: 'UTC', labelKey: 'timezone.region.utc' },
  { value: 'Europe/London', labelKey: 'timezone.region.ukIreland' },
  { value: 'Europe/Berlin', labelKey: 'timezone.region.centralEurope' },
  { value: 'Europe/Helsinki', labelKey: 'timezone.region.easternEurope' },
  { value: 'Europe/Istanbul', labelKey: 'timezone.region.turkey' },
  { value: 'Europe/Moscow', labelKey: 'timezone.region.moscow' },
  { value: 'Asia/Dubai', labelKey: 'timezone.region.gulf' },
  { value: 'Asia/Kabul', labelKey: 'timezone.region.afghanistan' },
  { value: 'Asia/Karachi', labelKey: 'timezone.region.pakistan' },
  { value: 'Asia/Kolkata', labelKey: 'timezone.region.india' },
  { value: 'Asia/Kathmandu', labelKey: 'timezone.region.nepal' },
  { value: 'Asia/Dhaka', labelKey: 'timezone.region.bangladesh' },
  { value: 'Asia/Yangon', labelKey: 'timezone.region.myanmar' },
  { value: 'Asia/Bangkok', labelKey: 'timezone.region.indochina' },
  { value: 'Asia/Jakarta', labelKey: 'timezone.region.westernIndonesia' },
  { value: 'Asia/Shanghai', labelKey: 'timezone.region.china' },
  { value: 'Asia/Singapore', labelKey: 'timezone.region.singapore' },
  { value: 'Asia/Tokyo', labelKey: 'timezone.region.japan' },
  { value: 'Asia/Seoul', labelKey: 'timezone.region.korea' },
  { value: 'Australia/Perth', labelKey: 'timezone.region.australianWestern' },
  { value: 'Australia/Adelaide', labelKey: 'timezone.region.australianCentral' },
  { value: 'Australia/Sydney', labelKey: 'timezone.region.australianEastern' },
  { value: 'Pacific/Guadalcanal', labelKey: 'timezone.region.solomon' },
  { value: 'Pacific/Auckland', labelKey: 'timezone.region.newZealand' },
  { value: 'Pacific/Fiji', labelKey: 'timezone.region.fiji' },
  { value: 'Pacific/Chatham', labelKey: 'timezone.region.chatham' },
  { value: 'Pacific/Kiritimati', labelKey: 'timezone.region.lineIslands' },
]

export const RUSSIAN_TIMEZONE_VALUES = new Set(RUSSIAN_TIMEZONES.map((r) => r.value))

/** @param {string} offsetPart e.g. GMT+3, UTC+03:00 */
function parseLongOffsetToMinutes(offsetPart) {
  if (!offsetPart || typeof offsetPart !== 'string') return null
  if (/^(?:GMT|UTC)$/i.test(offsetPart.trim())) return 0
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

function formatUtcOffsetStandard(minutes) {
  if (minutes == null) return 'UTC'
  const sign = minutes >= 0 ? '+' : '−'
  const abs = Math.abs(minutes)
  const h = Math.floor(abs / 60)
  const m = abs % 60
  return `UTC${sign}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

function buildTimezoneLabel(value, cityLine, at, t) {
  const utcMin = timezoneOffsetMinutesFromUtc(value, at)
  const relMin = minutesRelativeToMsk(value, at)
  const utcStr = formatUtcOffsetMinutes(utcMin)
  const mskStr = formatMskCompact(relMin, t)
  return `${cityLine} — ${utcStr}, ${mskStr}`
}

function getInternationalTimezoneSelectOptions(t) {
  const ref = new Date()
  return INTERNATIONAL_TIMEZONES
    .map(({ value, labelKey }) => {
      const offsetUtc = timezoneOffsetMinutesFromUtc(value, ref) ?? 0
      return {
        value,
        label: `(${formatUtcOffsetStandard(offsetUtc)}) ${t(labelKey)}`,
        offsetUtc,
      }
    })
    .sort((a, b) => a.offsetUtc - b.offsetUtc || a.value.localeCompare(b.value))
    .map(({ value, label }) => ({ value, label }))
}

/**
 * @returns {{ value: string, label: string }[]}
 */
export function getTimezoneSelectOptions(t, market = 'ru') {
  if (market === 'io') return getInternationalTimezoneSelectOptions(t)

  const ref = new Date()
  const rows = RUSSIAN_TIMEZONES.map(({ value, cityKey }) => ({
    value,
    label: buildTimezoneLabel(value, t(cityKey), ref, t),
    offsetUtc: timezoneOffsetMinutesFromUtc(value, ref) ?? 0,
  }))
  rows.sort((a, b) => a.offsetUtc - b.offsetUtc)
  return rows.map(({ value, label }) => ({ value, label }))
}
