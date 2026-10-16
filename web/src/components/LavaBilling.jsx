import { useEffect, useState } from 'react'
import { cancelSubscription, createSubscription, getBillingCheckout, getBillingHistory } from '../adapters/billing'
import { useI18n } from '../i18n'
import { BILLING_SUPPORT_EMAIL } from '../billingSupport'

export const LAVA_CONSENT_VERSION = '2026-10-16'
export const usdPrice = (minor) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(Number(minor || 0) / 100)

export function BillingPlanOptions({ plans, billing, onSelect, busy, requestedCode }) {
  const { t } = useI18n()
  return <div className="plan-list">{plans.map((plan) => {
    const current = billing?.status === 'active' && billing?.plan_code === plan.code
    const international = plan.price_usd_minor != null
    const free = plan.code === 'free'
    return <div key={plan.code} className={requestedCode === plan.code ? 'plan-item plan-item-requested' : 'plan-item'}>
      <div className="plan-item-top"><div className="plan-info">
        <strong>{plan.display_name || plan.code} {current && <span className="plan-requested-badge current">{t('settings.plan.current')}</span>}</strong>
        <span className="plan-price">{free ? t('common.free') : international ? `${usdPrice(plan.price_usd_minor)} / ${t('billing.lava.period')}` : `${plan.price_rub ? `${plan.price_rub}₽` : ''}${plan.price_rub && plan.price_stars ? ` ${t('common.or')} ` : ''}${plan.price_stars ? `${plan.price_stars}⭐` : ''}${t('channels.tariff.priceMonthly')}`}</span>
      </div>{!current && <div className="plan-actions">
        {free ? <button className="btn btn-small" type="button" disabled={busy} onClick={() => onSelect(plan, international || billing?.payment_provider === 'lava' ? 'lava' : 'tbank')}>{t('common.choose')}</button> : <>
          {international && <button className="btn btn-small" type="button" disabled={busy || (!plan.lava_available && billing?.payment_provider !== 'lava')} onClick={() => onSelect(plan, 'lava')}>{t('billing.lava.pay')}</button>}
          {!international && plan.price_rub > 0 && <button className="btn btn-small" type="button" disabled={busy} onClick={() => onSelect(plan, 'tbank')}>{t('common.choose')}</button>}
          {plan.price_stars > 0 && billing?.payment_provider !== 'lava' && <button className="btn btn-small btn-secondary" type="button" disabled={busy} onClick={() => onSelect(plan, 'stars')}>{plan.price_stars} ⭐</button>}
          {international && !plan.lava_available && <span className="muted">{t('billing.lava.unavailable')}</span>}
        </>}
      </div>}</div>
    </div>
  })}</div>
}

export function LavaPlanDialog({ plan, workspaceId, workspaceName, billing, onClose, onUpdated, withBillingEmail }) {
  const { t } = useI18n()
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const active = billing?.status === 'active' && billing?.plan_code !== 'free'
  const legacy = active && billing?.payment_provider !== 'lava'
  const free = plan.code === 'free'
  const run = async () => {
    setBusy(true)
    setError('')
    try {
      if (active) {
        await cancelSubscription(workspaceId, { target_plan_code: plan.code })
        onUpdated?.()
        onClose()
        return
      }
      const response = await createSubscription(workspaceId, {
        plan_code: plan.code, provider: 'lava', consent_version: LAVA_CONSENT_VERSION,
      })
      if (!response.payment_url) throw new Error(t('billing.lava.checking'))
      window.location.assign(response.payment_url)
    } catch (e) {
      if (e.details?.checkout_id) {
        window.location.assign(`/workspaces/${encodeURIComponent(workspaceId)}/settings?billing=lava_return&checkout=${encodeURIComponent(e.details.checkout_id)}`)
        return
      }
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-content" role="dialog" aria-modal="true" aria-labelledby="lava-billing-title" onClick={(event) => event.stopPropagation()}>
        <h3 id="lava-billing-title">{active ? t('billing.lava.changeTitle') : t('billing.lava.title')}</h3>
        <p>{t('billing.lava.workspace', { name: workspaceName || workspaceId })}</p>
        <p><strong>{plan.display_name || plan.code}</strong>{!free && ` · ${usdPrice(plan.price_usd_minor)} / ${t('billing.lava.period')}`}</p>
        <p>{legacy ? t('billing.lava.legacyChange') : active ? t('billing.lava.scheduledChange') : t('billing.lava.checkoutDescription')}</p>
        {billing?.current_period_end && active && <p>{t('billing.lava.paidUntil', { date: new Date(billing.current_period_end).toLocaleDateString() })}</p>}
        {!active && !free && (
          <label className="tbank-consent-checkbox-row">
            <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
            <span>{t('billing.lava.consent', { price: usdPrice(plan.price_usd_minor) })} <a href="/terms" target="_blank" rel="noopener noreferrer">{t('billing.lava.terms')}</a></span>
          </label>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <p className="muted">{t('billing.lava.support')} <a href={`mailto:${BILLING_SUPPORT_EMAIL}`}>{BILLING_SUPPORT_EMAIL}</a></p>
        <div className="inline-actions">
          <button className="btn btn-secondary" type="button" disabled={busy} onClick={onClose}>{t('common.cancel')}</button>
          {!free || active ? <button className="btn" type="button" disabled={busy || (!active && (!consent || !plan.lava_available))}
            onClick={() => active ? run() : withBillingEmail ? withBillingEmail(run) : run()}>
            {busy ? t('common.loading') : active ? t('billing.lava.scheduleChange') : t('billing.lava.pay')}
          </button> : null}
        </div>
      </div>
    </div>
  )
}

export function LavaPaymentStatus({ workspaceId, checkoutId, onPaid }) {
  const { t } = useI18n()
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!workspaceId || !checkoutId) return undefined
    let cancelled = false
    let timer
    let attempts = 0
    const poll = async () => {
      try {
        const response = await getBillingCheckout(workspaceId, checkoutId, true)
        if (cancelled) return
        setResult(response)
        setError('')
        if (response.status === 'succeeded') {
          onPaid?.(response)
          return
        }
        if (['failed', 'expired', 'review'].includes(response.status)) return
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
      if (!cancelled && ++attempts < 60) timer = window.setTimeout(poll, 10000)
    }
    void poll()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [workspaceId, checkoutId, onPaid])
  if (!checkoutId) return null
  const key = result?.status === 'succeeded' ? 'success' : ['failed', 'expired', 'review'].includes(result?.status) ? result.status : 'checking'
  return <div className="card" role="status"><p>{t(`billing.lava.${key}`)}</p>{result?.status === 'succeeded' && result.kind === 'migration' && <a className="btn" href={`/workspaces/${workspaceId}/channels`}>{t('billing.lava.continueImport')}</a>}{error && <p className="error">{error}</p>}</div>
}

export function LavaBillingDetails({ workspaceId, billing, canManage, onUpdated }) {
  const { t } = useI18n()
  const [history, setHistory] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const stopRenewal = async () => {
    setBusy(true)
    setError('')
    try {
      await cancelSubscription(workspaceId, { target_plan_code: 'free' })
      onUpdated?.()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  useEffect(() => {
    if (!workspaceId) return
    let cancelled = false
    getBillingHistory(workspaceId).then((response) => { if (!cancelled) setHistory(response.items || []) })
      .catch((e) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [workspaceId, billing?.payment_provider, billing?.current_period_end, billing?.status])
  if (billing?.payment_provider !== 'lava' && !history.some((row) => row.provider === 'lava')) return null
  return <section className="card">
    <h3>{t('billing.lava.details')}</h3>
    {billing.current_period_end && <p>{t('billing.lava.paidUntil', { date: new Date(billing.current_period_end).toLocaleDateString() })}</p>}
    <p>{billing.provider_cancel_pending ? t('billing.lava.cancelPending') : billing.cancel_at_period_end ? t('billing.lava.cancelled') : t('billing.lava.autoRenew')}</p>
    {canManage && billing.payment_provider === 'lava' && !billing.cancel_at_period_end && <button type="button" className="btn btn-secondary" disabled={busy || billing.provider_cancel_pending} onClick={stopRenewal}>{t('billing.lava.scheduleChange')}</button>}
    {billing.next_plan_code && billing.next_plan_code !== 'free' && <p>{t('billing.lava.nextPlan', { plan: billing.next_plan_code })}</p>}
    {error && <p className="error">{error}</p>}
    {history.length > 0 && <table><thead><tr><th>{t('billing.lava.date')}</th><th>{t('billing.lava.amount')}</th><th>{t('billing.lava.status')}</th></tr></thead>
      <tbody>{history.map((row) => <tr key={row.id}><td>{new Date(row.created_at).toLocaleDateString()}</td><td>{row.amount_minor == null ? '—' : row.currency === 'USD' ? usdPrice(row.amount_minor) : `${row.currency === 'XTR' ? row.amount_minor : row.amount_minor / 100} ${row.currency}`}</td><td>{row.disputed ? t('billing.lava.disputed') : row.refunded_amount_minor ? `${t('billing.lava.refunded')}: ${usdPrice(row.refunded_amount_minor)}` : t(`billing.lava.payment.${row.status}`)}</td></tr>)}</tbody>
    </table>}
  </section>
}
