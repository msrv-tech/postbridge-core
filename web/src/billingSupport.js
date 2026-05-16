const raw = typeof import.meta.env?.VITE_BILLING_SUPPORT_EMAIL === 'string'
  ? import.meta.env.VITE_BILLING_SUPPORT_EMAIL.trim()
  : ''

export const BILLING_SUPPORT_EMAIL = raw || 'support@example.com'

export function formatSubscriptionPeriod(period, locale = 'ru') {
  const p = String(period || 'month').toLowerCase()
  const normalizedLocale = String(locale || 'ru').toLowerCase()
  const en = normalizedLocale.startsWith('en')

  if (p === 'month' || p === 'monthly') return en ? 'every month' : 'каждый месяц'
  if (p === 'year' || p === 'yearly' || p === 'annual') return en ? 'every year' : 'каждый год'
  if (p === 'week' || p === 'weekly') return en ? 'every week' : 'каждую неделю'
  if (p === 'day' || p === 'daily') return en ? 'every day' : 'каждый день'
  return en ? `period: ${period || 'month'}` : `периодичность: ${period || 'month'}`
}

export function formatSubscriptionPeriodRu(period) {
  return formatSubscriptionPeriod(period, 'ru')
}
