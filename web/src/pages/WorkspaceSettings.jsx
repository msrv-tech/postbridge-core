import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link, useParams, useNavigate, useLocation } from 'react-router-dom'
import { updateCurrentUser } from '../adapters/account'
import { getWorkspaceAgentPolicy, upsertWorkspaceAgentPolicy } from '../adapters/agent'
import { getDashboardSummary } from '../adapters/dashboard'
import { dispatchPublicationTarget, listPublicationTargetProjections } from '../adapters/publicationTargets'
import { isSelfhostMode } from '../adapters/runtime'
import { getVersionCheck } from '../adapters/version'
import { getWorkspaceSettings, updateWorkspaceSettings } from '../adapters/workspaceSettings'
import { useAuth } from '../useAuth'
import AppShell from '../components/AppShell'
import { BILLING_SUPPORT_EMAIL, formatSubscriptionPeriod } from '../billingSupport'
import {
  cancelSubscription,
  createSubscription,
  isBillingEnabled,
  listBillingPlans,
  requestBillingEmail,
  verifyBillingEmail,
} from '../adapters/billing'
import { getTimezoneSelectOptions, RUSSIAN_TIMEZONE_VALUES } from '../timezoneOptions'
import { reachMetrikaGoal } from '../metrika'
import { useI18n } from '../i18n'
import { clearToken } from '../adapters/sessionToken'

function formatDate(iso, locale) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(locale, {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

const pendingSubscriptionGoalKey = (workspaceId) => `postbridge_pending_subscription_goal_${workspaceId}`

function readPendingSubscriptionGoal(workspaceId) {
  if (typeof window === 'undefined' || !workspaceId) return null
  try {
    const raw = window.localStorage.getItem(pendingSubscriptionGoalKey(workspaceId))
    const parsed = raw ? JSON.parse(raw) : null
    if (parsed?.started_at && Date.now() - parsed.started_at > 24 * 60 * 60 * 1000) {
      clearPendingSubscriptionGoal(workspaceId)
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function writePendingSubscriptionGoal(workspaceId, data) {
  if (typeof window === 'undefined' || !workspaceId) return
  window.localStorage.setItem(
    pendingSubscriptionGoalKey(workspaceId),
    JSON.stringify({ ...data, started_at: Date.now() }),
  )
}

function clearPendingSubscriptionGoal(workspaceId) {
  if (typeof window === 'undefined' || !workspaceId) return
  window.localStorage.removeItem(pendingSubscriptionGoalKey(workspaceId))
}

function clampPercent(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return null
  return Math.max(0, Math.min(100, Math.round(value)))
}

function buildAiTokenRemainder(billing, t) {
  if (!billing) return null
  const isFree = billing.plan_code === 'free'
  const used = Number(
    isFree
      ? (billing.ai_content_tokens_used_day ?? billing.ai_content_gitsell_tokens_used_day)
      : (billing.ai_content_tokens_used_month ?? billing.ai_content_gitsell_tokens_used_month),
  )
  const limit = Number(
    isFree
      ? (billing.ai_content_tokens_limit_day ?? billing.ai_content_gitsell_tokens_limit_day)
      : (billing.ai_content_tokens_limit_month ?? billing.ai_content_gitsell_tokens_limit_month),
  )
  if (!Number.isFinite(limit) || limit <= 0) {
    return {
      label: isFree ? t('settings.aiTokens.daily') : t('settings.aiTokens.monthly'),
      percent: null,
    }
  }
  const safeUsed = Number.isFinite(used) ? Math.max(0, used) : 0
  return {
    label: isFree ? t('settings.aiTokens.daily') : t('settings.aiTokens.monthly'),
    percent: clampPercent(((limit - safeUsed) / limit) * 100),
  }
}

function normalizeWorkspaceSettings(settings) {
  return {
    image_style_prompt: settings?.image_style_prompt || '',
  }
}

export default function WorkspaceSettings() {
  const { locale, t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { user, loading: authLoading, refreshUser } = useAuth()

  const [timezone, setTimezone] = useState('')
  const [tzSaving, setTzSaving] = useState(false)
  const [tzError, setTzError] = useState('')
  const [tzOk, setTzOk] = useState('')

  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [deliveryLoading, setDeliveryLoading] = useState(true)
  const [deliveryError, setDeliveryError] = useState('')
  const [dispatchingId, setDispatchingId] = useState(null)
  const [billingSummary, setBillingSummary] = useState(null)
  const [tariffModal, setTariffModal] = useState(false)
  const [tariffInitialPlanCode, setTariffInitialPlanCode] = useState('')
  const [plans, setPlans] = useState([])
  const [tariffLoading, setTariffLoading] = useState(false)
  const [tariffError, setTariffError] = useState('')
  const [tariffStarsWaiting, setTariffStarsWaiting] = useState(null)
  const [tbankSubscriptionWaiting, setTbankSubscriptionWaiting] = useState(null)
  const [billingEmailModal, setBillingEmailModal] = useState(null)
  const [tbankSubscriptionConsentModal, setTbankSubscriptionConsentModal] = useState(null)
  const [tbankSubscriptionConsentChecked, setTbankSubscriptionConsentChecked] = useState(false)
  const [workspaceAgentPolicy, setWorkspaceAgentPolicy] = useState({
    editor_instructions: '',
    search_instructions: '',
    preferred_domains: [],
    blocked_domains: [],
    blocked_url_patterns: [],
  })
  const [agentPolicyLoading, setAgentPolicyLoading] = useState(true)
  const [agentPolicySaving, setAgentPolicySaving] = useState(false)
  const [agentPolicyError, setAgentPolicyError] = useState('')
  const [agentPolicyOk, setAgentPolicyOk] = useState('')
  const [workspaceSettings, setWorkspaceSettings] = useState({ image_style_prompt: '' })
  const [workspaceSettingsLoading, setWorkspaceSettingsLoading] = useState(true)
  const [workspaceSettingsError, setWorkspaceSettingsError] = useState('')
  const [versionCheck, setVersionCheck] = useState(null)
  const [versionLoading, setVersionLoading] = useState(false)
  const [versionError, setVersionError] = useState('')
  const [showUpdateCommand, setShowUpdateCommand] = useState(false)
  const [updateCommandCopied, setUpdateCommandCopied] = useState(false)

  const preferredDomainsText = useMemo(
    () => (workspaceAgentPolicy.preferred_domains || []).join('\n'),
    [workspaceAgentPolicy]
  )
  const blockedDomainsText = useMemo(
    () => (workspaceAgentPolicy.blocked_domains || []).join('\n'),
    [workspaceAgentPolicy]
  )
  const billingEnabled = isBillingEnabled(user)
  const aiTokenRemainder = buildAiTokenRemainder(billingSummary?.billing, t)

  const tzSelectOptions = useMemo(() => {
    const base = getTimezoneSelectOptions(t)
    const saved = (user?.profile_timezone || '').trim()
    if (saved && !RUSSIAN_TIMEZONE_VALUES.has(saved)) {
      return [
        ...base,
        {
          value: saved,
          label: t('settings.timezone.savedOutsideCatalog', { timezone: saved }),
        },
      ]
    }
    return base
  }, [t, user?.profile_timezone])

  useEffect(() => {
    if (user?.profile_timezone) setTimezone(user.profile_timezone)
    else setTimezone('')
  }, [user])

  const loadDelivery = useCallback(() => {
    if (!workspaceId) return
    setDeliveryError('')
    setDeliveryLoading(true)
    listPublicationTargetProjections(workspaceId)
      .then((r) => {
        setItems(r.items || [])
        setTotal(r.total ?? 0)
      })
      .catch((e) => setDeliveryError(e.message))
      .finally(() => setDeliveryLoading(false))
  }, [workspaceId])

  useEffect(() => {
    loadDelivery()
  }, [loadDelivery])

  const loadBillingSummary = useCallback(() => {
    if (!workspaceId) return
    getDashboardSummary(workspaceId)
      .then((r) => setBillingSummary(r))
      .catch(() => {})
  }, [workspaceId])

  useEffect(() => {
    loadBillingSummary()
  }, [loadBillingSummary])

  const loadVersionCheck = useCallback(() => {
    if (!isSelfhostMode()) return
    setVersionLoading(true)
    setVersionError('')
    getVersionCheck()
      .then((result) => {
        setVersionCheck(result)
        setShowUpdateCommand(false)
        setUpdateCommandCopied(false)
      })
      .catch((e) => setVersionError(e.message || t('settings.updates.loadFailed')))
      .finally(() => setVersionLoading(false))
  }, [t])

  useEffect(() => {
    loadVersionCheck()
  }, [loadVersionCheck])

  useEffect(() => {
    if (!workspaceId) return
    setWorkspaceSettingsLoading(true)
    setWorkspaceSettingsError('')
    getWorkspaceSettings(workspaceId)
      .then((settings) => setWorkspaceSettings(normalizeWorkspaceSettings(settings)))
      .catch((e) => setWorkspaceSettingsError(e.message || t('settings.workspace.loadFailed')))
      .finally(() => setWorkspaceSettingsLoading(false))
  }, [workspaceId, t])

  useEffect(() => {
    if (!workspaceId) return
    setAgentPolicyLoading(true)
    setAgentPolicyError('')
    getWorkspaceAgentPolicy(workspaceId)
      .then((policy) =>
        setWorkspaceAgentPolicy({
          editor_instructions: policy.editor_instructions || '',
          search_instructions: policy.search_instructions || '',
          preferred_domains: Array.isArray(policy.preferred_domains) ? policy.preferred_domains : [],
          blocked_domains: Array.isArray(policy.blocked_domains) ? policy.blocked_domains : [],
          blocked_url_patterns: Array.isArray(policy.blocked_url_patterns) ? policy.blocked_url_patterns : [],
        })
      )
      .catch((e) => setAgentPolicyError(e.message || t('settings.agentPolicy.loadFailed')))
      .finally(() => setAgentPolicyLoading(false))
  }, [workspaceId, t])

  useEffect(() => {
    if (!tariffStarsWaiting || !workspaceId) return
    const interval = setInterval(async () => {
      try {
        const s = await getDashboardSummary(workspaceId)
        setBillingSummary(s)
        if (s?.billing?.plan_code === tariffStarsWaiting && s?.billing?.status === 'active') {
          reachMetrikaGoal('paid_subscription_success', {
            plan: tariffStarsWaiting,
            provider: 'stars',
            workspace_id: workspaceId,
          })
          clearPendingSubscriptionGoal(workspaceId)
          setTariffStarsWaiting(null)
          setTariffModal(false)
          clearInterval(interval)
        }
      } catch {
        // ignore
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [tariffStarsWaiting, workspaceId])

  useEffect(() => {
    const pending = readPendingSubscriptionGoal(workspaceId)
    if (pending?.provider === 'tbank' && pending?.plan) {
      setTbankSubscriptionWaiting(pending)
    }
  }, [workspaceId])

  useEffect(() => {
    if (!tbankSubscriptionWaiting || !workspaceId) return
    const interval = setInterval(async () => {
      try {
        const s = await getDashboardSummary(workspaceId)
        setBillingSummary(s)
        const billing = s?.billing
        if (
          billing?.plan_code === tbankSubscriptionWaiting.plan &&
          billing?.status === 'active'
        ) {
          reachMetrikaGoal('paid_subscription_success', {
            plan: tbankSubscriptionWaiting.plan,
            provider: 'tbank',
            workspace_id: workspaceId,
          })
          clearPendingSubscriptionGoal(workspaceId)
          setTbankSubscriptionWaiting(null)
          clearInterval(interval)
        }
      } catch {
        // ignore
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [tbankSubscriptionWaiting, workspaceId])

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  const handleTimezoneSubmit = async (e) => {
    e.preventDefault()
    setTzError('')
    setTzOk('')
    setTzSaving(true)
    try {
      const body =
        timezone && timezone.trim()
          ? { timezone: timezone.trim() }
          : { timezone: null }
      await updateCurrentUser(body)
      setTzOk(t('settings.saved'))
      await refreshUser()
    } catch (err) {
      setTzError(err.message || t('settings.saveError'))
    } finally {
      setTzSaving(false)
    }
  }

  const handleDispatch = async (targetId) => {
    setDispatchingId(targetId)
    setDeliveryError('')
    try {
      await dispatchPublicationTarget(workspaceId, targetId)
      loadDelivery()
    } catch (e) {
      setDeliveryError(e.message)
    } finally {
      setDispatchingId(null)
    }
  }

  const handleUpdateClick = () => {
    setShowUpdateCommand((value) => !value)
    setUpdateCommandCopied(false)
  }

  const handleCopyUpdateCommand = async () => {
    if (!versionCheck?.update_command || typeof navigator === 'undefined' || !navigator.clipboard) return
    await navigator.clipboard.writeText(versionCheck.update_command)
    setUpdateCommandCopied(true)
  }

  const parseLineList = (raw) =>
    String(raw || '')
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean)

  const handleAgentPolicySubmit = async (e) => {
    e.preventDefault()
    if (!workspaceId) return
    setAgentPolicySaving(true)
    setAgentPolicyError('')
    setAgentPolicyOk('')
    setWorkspaceSettingsError('')
    try {
      const payload = {
        editor_instructions: workspaceAgentPolicy.editor_instructions || '',
        search_instructions: workspaceAgentPolicy.search_instructions || '',
        preferred_domains: workspaceAgentPolicy.preferred_domains || [],
        blocked_domains: workspaceAgentPolicy.blocked_domains || [],
        blocked_url_patterns: workspaceAgentPolicy.blocked_url_patterns || [],
      }
      const [policyResult, settingsResult] = await Promise.allSettled([
        upsertWorkspaceAgentPolicy(workspaceId, payload),
        updateWorkspaceSettings(workspaceId, {
          image_style_prompt: workspaceSettings?.image_style_prompt || '',
        }),
      ])
      if (policyResult.status === 'fulfilled') {
        const saved = policyResult.value
        setWorkspaceAgentPolicy({
          editor_instructions: saved.editor_instructions || '',
          search_instructions: saved.search_instructions || '',
          preferred_domains: Array.isArray(saved.preferred_domains) ? saved.preferred_domains : [],
          blocked_domains: Array.isArray(saved.blocked_domains) ? saved.blocked_domains : [],
          blocked_url_patterns: Array.isArray(saved.blocked_url_patterns) ? saved.blocked_url_patterns : [],
        })
      } else {
        setAgentPolicyError(policyResult.reason?.message || t('settings.saveError'))
      }
      if (settingsResult.status === 'fulfilled') {
        setWorkspaceSettings(normalizeWorkspaceSettings(settingsResult.value))
      } else {
        setWorkspaceSettingsError(settingsResult.reason?.message || t('settings.saveError'))
      }
      if (policyResult.status === 'fulfilled' && settingsResult.status === 'fulfilled') {
        setAgentPolicyOk(t('settings.agentPolicy.saved'))
      }
    } finally {
      setAgentPolicySaving(false)
    }
  }

  const openTariffModal = (targetPlanCode = '') => {
    if (!billingEnabled) return
    reachMetrikaGoal('tariff_modal_opened', {
      plan: String(targetPlanCode || '').trim().toLowerCase() || undefined,
      workspace_id: workspaceId,
    })
    setTariffInitialPlanCode(String(targetPlanCode || '').trim().toLowerCase())
    setTariffModal(true)
    setTbankSubscriptionConsentModal(null)
    setTariffError('')
    setPlans([])
    setTariffLoading(true)
    listBillingPlans(workspaceId)
      .then((r) => setPlans(r.items || []))
      .catch((e) => setTariffError(e.message))
      .finally(() => setTariffLoading(false))
  }

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const billingParam = params.get('billing')
    if (!billingEnabled) {
      if (billingParam) {
        params.delete('billing')
        params.delete('plan')
        const nextSearch = params.toString()
        navigate(location.pathname + (nextSearch ? `?${nextSearch}` : ''), { replace: true })
      }
      return
    }
    if (billingParam === 'subscription_return') {
      const pending = readPendingSubscriptionGoal(workspaceId)
      if (pending?.provider === 'tbank' && pending?.plan) {
        setTbankSubscriptionWaiting(pending)
      }
      params.delete('billing')
      const nextSearch = params.toString()
      navigate(location.pathname + (nextSearch ? `?${nextSearch}` : ''), { replace: true })
      return
    }
    if (billingParam === 'card_attach_error') {
      clearPendingSubscriptionGoal(workspaceId)
      params.delete('billing')
      const nextSearch = params.toString()
      navigate(location.pathname + (nextSearch ? `?${nextSearch}` : ''), { replace: true })
      return
    }
    if (params.get('billing') !== 'change-plan') return
    openTariffModal(params.get('plan') || '')
    params.delete('billing')
    params.delete('plan')
    const nextSearch = params.toString()
    navigate(location.pathname + (nextSearch ? `?${nextSearch}` : ''), { replace: true })
  }, [billingEnabled, location.pathname, location.search, navigate])

  const openBillingEmailModalThen = (action) => {
    if (!user?.billing_email_required) {
      void action()
      return
    }
    setBillingEmailModal({ step: 'email', email: '', code: '', error: '', devCode: null, onDone: action })
  }

  const submitBillingEmailRequest = async () => {
    const em = (billingEmailModal?.email || '').trim()
    if (!em) {
      setBillingEmailModal((m) => (m ? { ...m, error: t('channels.billingEmail.empty') } : m))
      return
    }
    setBillingEmailModal((m) => (m ? { ...m, error: '' } : m))
    try {
      const r = await requestBillingEmail({ email: em })
      setBillingEmailModal((m) =>
        m ? { ...m, step: 'code', error: '', devCode: r.code || null } : m
      )
    } catch (e) {
      setBillingEmailModal((m) => (m ? { ...m, error: e.message } : m))
    }
  }

  const submitBillingEmailVerify = async () => {
    const raw = (billingEmailModal?.code || '').trim()
    if (raw.length !== 4 || !/^\d+$/.test(raw)) {
      setBillingEmailModal((m) => (m ? { ...m, error: t('channels.billingEmail.badCode') } : m))
      return
    }
    setBillingEmailModal((m) => (m ? { ...m, error: '' } : m))
    try {
      await verifyBillingEmail({ code: raw })
      await refreshUser()
      const run = billingEmailModal?.onDone
      setBillingEmailModal(null)
      if (run) await run()
    } catch (e) {
      setBillingEmailModal((m) => (m ? { ...m, error: e.message } : m))
    }
  }

  const closeBillingEmailModal = () => {
    setBillingEmailModal(null)
  }

  const executeSubscriptionCreate = async (plan, provider) => {
    if (!billingEnabled) return
    setTariffError('')
    setTariffLoading(true)
    try {
      const currentCode = billingSummary?.billing?.plan_code || ''
      const currentPlan = plans.find((x) => x.code === currentCode)
      const targetPrice = plan.price_rub || plan.price_stars || 0
      const currentPrice = currentPlan?.price_rub || currentPlan?.price_stars || 0
      if (targetPrice > currentPrice) {
        const runCreate = async () => {
          const res = await createSubscription(workspaceId, { plan_code: plan.code, provider })
          if (res.payment_url) {
            writePendingSubscriptionGoal(workspaceId, {
              plan: plan.code,
              provider,
            })
            reachMetrikaGoal('checkout_started', {
              plan: plan.code,
              provider,
              amount: plan.price_rub || null,
              currency: 'RUB',
              workspace_id: workspaceId,
            })
            window.location.href = res.payment_url
          } else if (res.invoice_url) {
            writePendingSubscriptionGoal(workspaceId, {
              plan: plan.code,
              provider,
            })
            reachMetrikaGoal('checkout_started', {
              plan: plan.code,
              provider,
              amount: plan.price_stars || null,
              currency: 'XTR',
              workspace_id: workspaceId,
            })
            window.open(res.invoice_url, '_blank')
            setTariffStarsWaiting(plan.code)
          } else {
            setTariffModal(false)
            loadBillingSummary()
          }
        }
        if (provider === 'tbank' && user?.billing_email_required) {
          setTbankSubscriptionConsentModal(null)
          openBillingEmailModalThen(runCreate)
          return
        }
        await runCreate()
      } else {
        await cancelSubscription(workspaceId, { target_plan_code: plan.code })
        setTariffModal(false)
        loadBillingSummary()
      }
    } catch (e) {
      setTariffError(e.message)
    } finally {
      setTariffLoading(false)
    }
  }

  const handlePlanSelect = (plan, provider) => {
    if (!billingEnabled) return
    setTariffError('')
    reachMetrikaGoal('plan_selected', {
      plan: plan.code,
      provider,
      source: 'workspace_settings',
      workspace_id: workspaceId,
    })
    const currentCode = billingSummary?.billing?.plan_code || ''
    const currentPlan = plans.find((x) => x.code === currentCode)
    const targetPrice = plan.price_rub || plan.price_stars || 0
    const currentPrice = currentPlan?.price_rub || currentPlan?.price_stars || 0
    const isPaidUpgrade = targetPrice > currentPrice
    if (provider === 'tbank' && plan.price_rub && isPaidUpgrade) {
      setTbankSubscriptionConsentChecked(false)
      setTbankSubscriptionConsentModal({ plan })
      return
    }
    void executeSubscriptionCreate(plan, provider)
  }

  if (authLoading || !user) {
    return (
      <AppShell title={t('settings.title')} user={user} onLogout={handleLogout}>
        <div className="card">
          <p className="muted">{t('common.loading')}</p>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell
      title={t('settings.title')}
      subtitle=""
      user={user}
      onLogout={handleLogout}
      showAdminLink={user.is_platform_admin}
    >
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <h3 className="h-small" style={{ marginTop: 0 }}>
          {t('settings.profile.title')}
        </h3>
        <div style={{ display: 'grid', gap: '1rem' }}>
          <div className="status-row" style={{ paddingTop: 0 }}>
            <span className="status-label">{t('settings.user')}</span>
            <div className="app-header-actions" style={{ justifyContent: 'flex-start' }}>
              <strong>{user.telegram_username ? `@${user.telegram_username}` : user.email || user.user_id}</strong>
              <button type="button" className="btn btn-secondary btn-small" onClick={handleLogout}>
                {t('settings.logout')}
              </button>
            </div>
          </div>
          {billingEnabled && (
            <div className="status-row">
              <span className="status-label">{t('settings.currentPlan')}</span>
              <div className="app-header-actions" style={{ justifyContent: 'flex-start' }}>
                <span className="plan-badge">{billingSummary?.billing?.plan_code || '—'}</span>
                <button type="button" className="btn btn-secondary btn-small" onClick={openTariffModal}>
                  {t('settings.changePlan')}
                </button>
              </div>
            </div>
          )}
          <div>
            <p className="muted post-editor-hint" style={{ marginTop: 0 }}>
              {t('settings.timezone.hint', { timezone: user.timezone })}
            </p>
            <form onSubmit={handleTimezoneSubmit}>
              <div
                className="form-group"
                style={{ display: 'flex', gap: '0.75rem', alignItems: 'end', flexWrap: 'wrap', marginBottom: 0 }}
              >
                <div style={{ flex: '1 1 20rem' }}>
                  <label htmlFor="tz-select">{t('settings.timezone.label')}</label>
                  <select
                    id="tz-select"
                    className="form-control"
                    value={timezone}
                    onChange={(e) => setTimezone(e.target.value)}
                  >
                    <option value="">{t('settings.timezone.default')}</option>
                    {tzSelectOptions.map(({ value, label }) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <button type="submit" className="btn" disabled={tzSaving}>
                  {tzSaving ? t('common.savingShort') : t('common.save')}
                </button>
              </div>
              {tzError && <p className="error">{tzError}</p>}
              {tzOk && <p className="muted">{tzOk}</p>}
            </form>
          </div>
        </div>
      </div>

      {isSelfhostMode() && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              justifyContent: 'space-between',
              gap: '1rem',
              flexWrap: 'wrap',
            }}
          >
            <div>
              <h3 className="h-small" style={{ marginTop: 0 }}>
                {t('settings.updates.title')}
              </h3>
              <p className="muted post-editor-hint">
                {t('settings.updates.text')}
              </p>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={loadVersionCheck}
              disabled={versionLoading}
            >
              {versionLoading ? t('common.checking') : t('common.refresh')}
            </button>
          </div>
          {versionError && <p className="error">{versionError}</p>}
          {!versionError && (
            <div className="status-row" style={{ alignItems: 'flex-start' }}>
              <span className="status-label">{t('settings.updates.current')}</span>
              <div className="app-header-actions" style={{ justifyContent: 'flex-start', alignItems: 'center' }}>
                <span className="plan-badge">{versionCheck?.current_version || '—'}</span>
                {versionCheck?.latest_version && (
                  <span className="muted">
                    {t('settings.updates.latest', { version: versionCheck.latest_version })}
                  </span>
                )}
                {versionCheck?.release_url && (
                  <a href={versionCheck.release_url} target="_blank" rel="noreferrer">
                    {t('settings.updates.releaseNotes')}
                  </a>
                )}
              </div>
            </div>
          )}
          {versionCheck?.check_status && versionCheck.check_status !== 'ok' && (
            <p className="muted post-editor-hint">
              {t('settings.updates.unavailable')}
            </p>
          )}
          {versionCheck?.check_status === 'ok' && !versionCheck.update_available && (
            <p className="muted post-editor-hint">
              {t('settings.updates.upToDate')}
            </p>
          )}
          {versionCheck?.update_available && (
            <div style={{ display: 'grid', gap: '0.75rem' }}>
              <div className="toolbar" style={{ marginTop: 0 }}>
                <span className="plan-requested-badge">
                  {t('settings.updates.available', { version: versionCheck.latest_version })}
                </span>
                <button type="button" className="btn btn-small" onClick={handleUpdateClick}>
                  {t('settings.updates.updateTo', { version: versionCheck.latest_version })}
                </button>
              </div>
              {showUpdateCommand && (
                <div className="empty-state" style={{ padding: '1rem' }}>
                  <p className="muted post-editor-hint" style={{ marginTop: 0 }}>
                    {t('settings.updates.commandText')}
                  </p>
                  <pre className="code-block" style={{ whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
                    {versionCheck.update_command}
                  </pre>
                  <button type="button" className="btn btn-secondary btn-small" onClick={handleCopyUpdateCommand}>
                    {updateCommandCopied ? t('settings.updates.copied') : t('settings.updates.copyCommand')}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <h3 className="h-small" style={{ marginTop: 0 }}>
          {t('settings.agents.title')}
        </h3>
        <p className="muted post-editor-hint">
          {t('settings.agents.text')}
        </p>
        {aiTokenRemainder && (
          <div className="settings-token-meter">
            <div className="settings-token-meter-head">
              <span>{aiTokenRemainder.label}</span>
              <strong>
                {aiTokenRemainder.percent == null ? t('settings.aiTokens.unlimited') : `${aiTokenRemainder.percent}%`}
              </strong>
            </div>
            {aiTokenRemainder.percent != null && (
              <div className="settings-token-meter-track" aria-hidden="true">
                <span style={{ width: `${aiTokenRemainder.percent}%` }} />
              </div>
            )}
          </div>
        )}
        <div className="toolbar">
          <Link to={`/workspaces/${workspaceId}/agents/topic-scout`} className="btn btn-secondary btn-small">
            {t('agents.nav.topicScout')}
          </Link>
          <Link to={`/workspaces/${workspaceId}/agents/candidates`} className="btn btn-secondary btn-small">
            {t('agents.nav.candidates')}
          </Link>
          {user.is_platform_admin && (
            <Link to={`/workspaces/${workspaceId}/agents/ops`} className="btn btn-secondary btn-small">
              {t('agentOps.title')}
            </Link>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <h3 className="h-small" style={{ marginTop: 0 }}>
          {t('settings.agentPolicy.title')}
        </h3>
        <p className="muted post-editor-hint">
          {t('settings.agentPolicy.text')}
          {user.is_platform_admin && (
            <>
              {' '}{t('settings.agentPolicy.adminPrefix')}{' '}
              <Link to={`/workspaces/${workspaceId}/agents/ops`}>{t('settings.agentPolicy.adminLink')}</Link>.
            </>
          )}
        </p>
        {agentPolicyLoading || workspaceSettingsLoading ? (
          <p className="muted">{t('common.loading')}</p>
        ) : (
          <form onSubmit={handleAgentPolicySubmit} style={{ display: 'grid', gap: '1rem' }}>
            <div className="form-group" style={{ marginBottom: 0 }}>
              <label htmlFor="workspace-image-style-prompt">
                {t('settings.workspace.imageStylePrompt')}
              </label>
              <textarea
                id="workspace-image-style-prompt"
                className="form-control settings-image-style-prompt"
                rows={4}
                value={workspaceSettings.image_style_prompt}
                onChange={(e) =>
                  setWorkspaceSettings((current) => ({
                    ...current,
                    image_style_prompt: e.target.value,
                  }))
                }
                placeholder={t('settings.workspace.imageStylePromptPlaceholder')}
              />
              <p className="muted post-editor-hint" style={{ marginBottom: 0 }}>
                {t('settings.workspace.imageStylePromptHint')}
              </p>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem' }}>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="agent-editor-instructions">{t('settings.agentPolicy.editor')}</label>
                <textarea
                  id="agent-editor-instructions"
                  className="form-control"
                  rows={5}
                  value={workspaceAgentPolicy.editor_instructions}
                  onChange={(e) =>
                    setWorkspaceAgentPolicy((current) => ({
                      ...current,
                      editor_instructions: e.target.value,
                    }))
                  }
                  placeholder={t('settings.agentPolicy.editorPlaceholder')}
                />
              </div>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="agent-search-instructions">{t('settings.agentPolicy.search')}</label>
                <textarea
                  id="agent-search-instructions"
                  className="form-control"
                  rows={5}
                  value={workspaceAgentPolicy.search_instructions}
                  onChange={(e) =>
                    setWorkspaceAgentPolicy((current) => ({
                      ...current,
                      search_instructions: e.target.value,
                    }))
                  }
                  placeholder={t('settings.agentPolicy.searchPlaceholder')}
                />
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem' }}>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="agent-preferred-domains">{t('settings.agentPolicy.preferredDomains')}</label>
                <textarea
                  id="agent-preferred-domains"
                  className="form-control"
                  rows={5}
                  value={preferredDomainsText}
                  onChange={(e) =>
                    setWorkspaceAgentPolicy((current) => ({
                      ...current,
                      preferred_domains: parseLineList(e.target.value),
                    }))
                  }
                  placeholder={'official.example\nblog.example.com'}
                />
                <p className="muted post-editor-hint" style={{ marginBottom: 0 }}>
                  {t('settings.agentPolicy.preferredDomainsHint')}
                </p>
              </div>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="agent-blocked-domains">{t('settings.agentPolicy.blockedDomains')}</label>
                <textarea
                  id="agent-blocked-domains"
                  className="form-control"
                  rows={5}
                  value={blockedDomainsText}
                  onChange={(e) =>
                    setWorkspaceAgentPolicy((current) => ({
                      ...current,
                      blocked_domains: parseLineList(e.target.value),
                    }))
                  }
                  placeholder={'pinterest.com\nreddit.com'}
                />
                <p className="muted post-editor-hint" style={{ marginBottom: 0 }}>
                  {t('settings.agentPolicy.blockedDomainsHint')}
                </p>
              </div>
            </div>
            <div className="toolbar" style={{ marginTop: 0 }}>
              <button type="submit" className="btn" disabled={agentPolicySaving}>
                {agentPolicySaving ? t('common.savingShort') : t('common.save')}
              </button>
              {user.is_platform_admin && (
                <Link to={`/workspaces/${workspaceId}/agents/ops`} className="btn btn-secondary btn-small">
                  {t('settings.agentPolicy.advanced')}
                </Link>
              )}
            </div>
            {agentPolicyError && <p className="error">{agentPolicyError}</p>}
            {workspaceSettingsError && <p className="error">{workspaceSettingsError}</p>}
            {agentPolicyOk && <p className="muted">{agentPolicyOk}</p>}
          </form>
        )}
      </div>

      <div className="card">
        <h3 className="h-small" style={{ marginTop: 0 }}>
          {t('settings.delivery.title')}
        </h3>
        <p className="muted post-editor-hint">{t('settings.delivery.text')}</p>

        <div className="toolbar" style={{ marginBottom: '1rem' }}>
          <button
            type="button"
            className="btn btn-secondary btn-small"
            onClick={loadDelivery}
            disabled={deliveryLoading}
          >
            {t('settings.delivery.refresh')}
          </button>
        </div>

        {deliveryLoading && <p className="muted">{t('common.loading')}</p>}
        {!deliveryLoading && deliveryError && <p className="error">{deliveryError}</p>}

        {!deliveryLoading && !deliveryError && (
          <p className="muted" style={{ marginBottom: '1rem' }}>
            {t('settings.delivery.total', { total })}
          </p>
        )}

        {!deliveryLoading && items.length === 0 && !deliveryError && (
          <div className="empty-state" style={{ padding: '0.5rem 0' }}>
            <p className="muted">
              {t('settings.delivery.empty')}
            </p>
          </div>
        )}

        {items.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('settings.delivery.coreId')}</th>
                  <th>{t('settings.delivery.status')}</th>
                  <th>{t('settings.delivery.platform')}</th>
                  <th>{t('settings.delivery.channel')}</th>
                  <th>{t('settings.delivery.updated')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.core_publication_target_id}>
                    <td>
                      <code style={{ fontSize: '0.85em', wordBreak: 'break-all' }}>
                        {row.core_publication_target_id}
                      </code>
                    </td>
                    <td>{row.status}</td>
                    <td>{row.platform || '—'}</td>
                    <td>
                      <code style={{ fontSize: '0.85em' }}>{row.channel_id || '—'}</code>
                    </td>
                    <td>{formatDate(row.updated_at, locale)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        disabled={dispatchingId === row.core_publication_target_id}
                        onClick={() => handleDispatch(row.core_publication_target_id)}
                      >
                        {dispatchingId === row.core_publication_target_id ? '…' : t('settings.delivery.retry')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {items.some((row) => row.error_code || row.error_message) && (
          <div style={{ marginTop: '1rem' }}>
            <h4 className="h-small">{t('settings.delivery.errors')}</h4>
            <ul className="muted" style={{ margin: 0, paddingLeft: '1.25rem' }}>
              {items
                .filter((row) => row.error_code || row.error_message)
                .map((row) => (
                  <li key={`${row.core_publication_target_id}-err`}>
                    <code>{row.core_publication_target_id}</code>: {row.error_code || ''}{' '}
                    {row.error_message || ''}
                  </li>
                ))}
            </ul>
          </div>
        )}
      </div>

      {billingEnabled && billingEmailModal && (
        <div className="modal-overlay" onClick={closeBillingEmailModal}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>{t('settings.billingEmail.title')}</h3>
            {billingEmailModal.step === 'email' ? (
              <>
                <p className="muted section-copy">
                  {t('settings.billingEmail.text')}
                </p>
                <div className="form-group">
                  <label htmlFor="billing-email-input">Email</label>
                  <input
                    id="billing-email-input"
                    type="email"
                    className="form-control"
                    value={billingEmailModal.email}
                    onChange={(e) =>
                      setBillingEmailModal((m) => (m ? { ...m, email: e.target.value } : m))
                    }
                    placeholder="you@example.com"
                  />
                </div>
              </>
            ) : (
              <>
                <p className="muted section-copy">
                  {t('settings.billingEmail.sent', { email: billingEmailModal.email })}
                </p>
                <div className="form-group">
                  <label htmlFor="billing-email-code-input">{t('settings.billingEmail.code')}</label>
                  <input
                    id="billing-email-code-input"
                    type="text"
                    inputMode="numeric"
                    maxLength={4}
                    className="form-control"
                    value={billingEmailModal.code}
                    onChange={(e) =>
                      setBillingEmailModal((m) => (m ? { ...m, code: e.target.value } : m))
                    }
                    placeholder="1234"
                  />
                </div>
                {billingEmailModal.devCode && (
                  <p className="muted section-copy">{t('channels.billingEmail.devCode', { code: billingEmailModal.devCode })}</p>
                )}
              </>
            )}
            {billingEmailModal.error && <p className="error">{billingEmailModal.error}</p>}
            <div className="inline-actions" style={{ marginTop: '1rem' }}>
              <button type="button" className="btn btn-secondary" onClick={closeBillingEmailModal}>
                {t('common.cancel')}
              </button>
              {billingEmailModal.step === 'email' ? (
                <button type="button" className="btn" onClick={() => void submitBillingEmailRequest()}>
                  {t('channels.billingEmail.send')}
                </button>
              ) : (
                <button type="button" className="btn" onClick={() => void submitBillingEmailVerify()}>
                  {t('common.confirm')}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {billingEnabled && tariffModal && (
        <div
          className="modal-overlay"
          onClick={() => {
            setTariffModal(false)
            setTariffInitialPlanCode('')
            setTariffStarsWaiting(null)
            setTbankSubscriptionConsentModal(null)
          }}
        >
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>{t('settings.changePlan')}</h3>
            {tariffStarsWaiting && (
              <p className="muted" style={{ marginBottom: '1rem' }}>
                {t('channels.tariff.waitingStars')}
              </p>
            )}
            {tariffLoading && !tariffStarsWaiting && <p className="muted">{t('channels.tariff.loadingPlans')}</p>}
            {tariffError && <p className="error">{tariffError}</p>}
            {!tariffLoading && plans.length > 0 && (
              <div className="plan-list">
                {plans.map((plan) => {
                  const isCurrent = billingSummary?.billing?.plan_code === plan.code
                  const isRequested = tariffInitialPlanCode === plan.code
                  const hasBothPrices = plan.price_rub && plan.price_stars
                  return (
                    <div
                      key={plan.code}
                      className={isRequested ? 'plan-item plan-item-requested' : 'plan-item'}
                    >
                      <div className="plan-item-top">
                        <div className="plan-info">
                          <strong>
                            {plan.display_name || plan.code}
                            {isCurrent && <span className="plan-requested-badge current">{t('settings.plan.current')}</span>}
                            {!isCurrent && isRequested && (
                              <span className="plan-requested-badge">{t('settings.plan.requested')}</span>
                            )}
                          </strong>
                          <span className="plan-price">
                            {plan.price_rub && plan.price_stars
                              ? `${plan.price_rub}₽ ${t('common.or')} ${plan.price_stars}⭐`
                              : plan.price_rub
                                ? `${plan.price_rub}₽`
                                : plan.price_stars
                                  ? `${plan.price_stars}⭐`
                                  : t('common.free')}
                            {plan.period && plan.period !== 'month' ? ` / ${plan.period}` : t('channels.tariff.priceMonthly')}
                          </span>
                        </div>
                        {!isCurrent && (
                          <div className="plan-actions">
                            {hasBothPrices ? (
                              <>
                                <button
                                  type="button"
                                  className="btn btn-small"
                                  onClick={() => handlePlanSelect(plan, 'tbank')}
                                  disabled={tariffLoading || tariffStarsWaiting}
                                >
                                  {plan.price_rub}₽
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-small btn-secondary"
                                  onClick={() => handlePlanSelect(plan, 'stars')}
                                  disabled={tariffLoading || tariffStarsWaiting}
                                >
                                  {plan.price_stars}⭐
                                </button>
                              </>
                            ) : plan.price_rub ? (
                              <button
                                type="button"
                                className="btn btn-small"
                                onClick={() => handlePlanSelect(plan, 'tbank')}
                                disabled={tariffLoading || tariffStarsWaiting}
                              >
                                {t('common.choose')}
                              </button>
                            ) : plan.price_stars ? (
                              <button
                                type="button"
                                className="btn btn-small"
                                onClick={() => handlePlanSelect(plan, 'stars')}
                                disabled={tariffLoading || tariffStarsWaiting}
                              >
                                {t('common.choose')}
                              </button>
                            ) : null}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
            <div className="inline-actions" style={{ marginTop: '1rem' }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  setTariffModal(false)
                  setTariffInitialPlanCode('')
                  setTariffStarsWaiting(null)
                  setTbankSubscriptionConsentModal(null)
                }}
              >
                {t('common.close')}
              </button>
            </div>
          </div>
        </div>
      )}

      {billingEnabled && tbankSubscriptionConsentModal && (
        <div
          className="modal-overlay"
          onClick={() => {
            if (!tariffLoading) setTbankSubscriptionConsentModal(null)
          }}
        >
          <div className="modal-content tbank-consent-modal" onClick={(e) => e.stopPropagation()}>
            <h3>{t('channels.tbank.title')}</h3>
            <p className="muted section-copy" style={{ marginBottom: '1rem' }}>
              {t('channels.tbank.text')}
            </p>
            <div className="tbank-consent-plan-box" role="status">
              <strong>{tbankSubscriptionConsentModal.plan.display_name || tbankSubscriptionConsentModal.plan.code}</strong>
              {' - '}
              <strong>{tbankSubscriptionConsentModal.plan.price_rub} ₽</strong>
              <br />
              {t('channels.tbank.period', {
                period: formatSubscriptionPeriod(tbankSubscriptionConsentModal.plan.period, t),
              })}
            </div>
            <label className="tbank-consent-checkbox-row" htmlFor="tbank-subscription-recurrent-consent">
              <input
                id="tbank-subscription-recurrent-consent"
                type="checkbox"
                checked={tbankSubscriptionConsentChecked}
                onChange={(e) => setTbankSubscriptionConsentChecked(e.target.checked)}
              />
              <span>
                {t('channels.tbank.consent')}
              </span>
            </label>
            <p className="billing-support-hint" style={{ marginTop: '1.25rem' }}>
              {t('channels.tbank.support', { email: BILLING_SUPPORT_EMAIL })}
            </p>
            <div className="inline-actions tbank-consent-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={tariffLoading}
                onClick={() => setTbankSubscriptionConsentModal(null)}
              >
                {t('common.back')}
              </button>
              <button
                type="button"
                className="btn"
                disabled={!tbankSubscriptionConsentChecked || tariffLoading}
                onClick={() => {
                  const plan = tbankSubscriptionConsentModal.plan
                  setTbankSubscriptionConsentModal(null)
                  void executeSubscriptionCreate(plan, 'tbank')
                }}
              >
                {t('channels.tbank.pay')}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  )
}
