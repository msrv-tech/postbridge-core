const raw = typeof import.meta.env?.VITE_BILLING_SUPPORT_EMAIL === 'string'
  ? import.meta.env.VITE_BILLING_SUPPORT_EMAIL.trim()
  : ''

export const BILLING_SUPPORT_EMAIL = raw || 'support@example.com'

export function formatSubscriptionPeriod(period, t) {
  const p = String(period || 'month').toLowerCase()

  if (p === 'month' || p === 'monthly') return t('billing.period.month')
  if (p === 'year' || p === 'yearly' || p === 'annual') return t('billing.period.year')
  if (p === 'week' || p === 'weekly') return t('billing.period.week')
  if (p === 'day' || p === 'daily') return t('billing.period.day')
  return t('billing.period.custom', { period: period || 'month' })
}
