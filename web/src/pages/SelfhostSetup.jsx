import { useEffect, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import {
  bootstrapSelfhost,
  getSelfhostSession,
  loginSelfhost,
  pollGitsellDeviceFlow,
  startGitsellDeviceFlow,
} from '../adapters/selfhostSetup'
import { setToken } from '../adapters/sessionToken'
import { isSelfhostMode } from '../adapters/runtime'
import { useI18n } from '../i18n'

const INTEGRATION_GROUPS = [
  {
    category: 'ai_gateway',
    titleKey: 'settings.integrations.ai.title',
    textKey: 'settings.integrations.ai.text',
    fields: [
      ['base_url', 'settings.integrations.ai.baseUrl', 'text', 'https://api.openai.com/v1'],
      ['api_key', 'settings.integrations.ai.apiKey', 'password', ''],
      ['default_model', 'settings.integrations.ai.defaultModel', 'text', 'gpt-5.4-mini'],
      ['image_model', 'settings.integrations.ai.imageModel', 'text', 'gpt-image-2'],
      ['image_size', 'settings.integrations.ai.imageSize', 'text', '1536x1024'],
    ],
  },
  {
    category: 'telegram_bot',
    titleKey: 'settings.integrations.telegramBot.title',
    textKey: 'settings.integrations.telegramBot.text',
    fields: [
      ['bot_token', 'settings.integrations.telegramBot.token', 'password', ''],
      ['bot_username', 'settings.integrations.telegramBot.username', 'text', ''],
    ],
  },
  {
    category: 'media_storage',
    titleKey: 'settings.integrations.media.title',
    textKey: 'settings.integrations.media.text',
    fields: [
      ['storage_type', 'settings.integrations.media.type', 'text', 'local'],
      ['media_base_url', 'settings.integrations.media.baseUrl', 'text', 'http://127.0.0.1:8000/media'],
      ['s3_bucket', 'settings.integrations.media.s3Bucket', 'text', ''],
      ['s3_endpoint_url', 'settings.integrations.media.s3Endpoint', 'text', ''],
      ['s3_access_key', 'settings.integrations.media.s3AccessKey', 'password', ''],
      ['s3_secret_key', 'settings.integrations.media.s3SecretKey', 'password', ''],
    ],
  },
]

const SELFHOST_INSTANCE_ID_KEY = 'postbridge.selfhost.instance_id'
const SETUP_STEPS = ['basics', 'ai', 'optional']

function selfhostInstanceId() {
  if (typeof window === 'undefined') return 'postbridge-selfhost'
  const existing = window.localStorage.getItem(SELFHOST_INSTANCE_ID_KEY)
  if (existing) return existing
  const generated = window.crypto?.randomUUID?.() || `postbridge-${Date.now()}-${Math.random().toString(16).slice(2)}`
  window.localStorage.setItem(SELFHOST_INSTANCE_ID_KEY, generated)
  return generated
}

function emptyIntegrations() {
  return INTEGRATION_GROUPS.reduce((acc, group) => {
    acc[group.category] = group.fields.reduce((fields, [name]) => ({ ...fields, [name]: '' }), {})
    return acc
  }, {})
}

function integrationPayload(values) {
  const out = {}
  if (
    values.ai_gateway.base_url
    || values.ai_gateway.api_key
    || values.ai_gateway.default_model
    || values.ai_gateway.image_model
    || values.ai_gateway.image_size
  ) {
    out.ai_gateway = {
      config: {
        base_url: values.ai_gateway.base_url || undefined,
        default_model: values.ai_gateway.default_model || undefined,
        image_model: values.ai_gateway.image_model || undefined,
        image_size: values.ai_gateway.image_size || undefined,
      },
      secret: { api_key: values.ai_gateway.api_key || undefined },
    }
  }
  if (values.telegram_bot.bot_token || values.telegram_bot.bot_username) {
    out.telegram_bot = {
      config: { bot_username: values.telegram_bot.bot_username || undefined },
      secret: { bot_token: values.telegram_bot.bot_token || undefined },
    }
  }
  if (Object.values(values.media_storage).some(Boolean)) {
    out.media_storage = {
      config: {
        storage_type: values.media_storage.storage_type || undefined,
        media_base_url: values.media_storage.media_base_url || undefined,
        s3_bucket: values.media_storage.s3_bucket || undefined,
        s3_endpoint_url: values.media_storage.s3_endpoint_url || undefined,
      },
      secret: {
        s3_access_key: values.media_storage.s3_access_key || undefined,
        s3_secret_key: values.media_storage.s3_secret_key || undefined,
      },
    }
  }
  return out
}

export default function SelfhostSetup() {
  const { isLocaleLocked, locale, setLocale, supportedLocales, t } = useI18n()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [mode, setMode] = useState('setup')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [admin, setAdmin] = useState({ username: 'admin', password: '' })
  const [tenantName, setTenantName] = useState('Postbridge Self-host')
  const [integrations, setIntegrations] = useState(emptyIntegrations)
  const [manualAiOpen, setManualAiOpen] = useState(false)
  const [setupStep, setSetupStep] = useState(0)
  const [aiSkipped, setAiSkipped] = useState(false)
  const [gitsellFlow, setGitsellFlow] = useState(null)
  const [gitsellError, setGitsellError] = useState('')

  useEffect(() => {
    getSelfhostSession()
      .then((result) => {
        if (result?.authenticated) {
          navigate('/workspaces/local/channels', { replace: true })
          return
        }
        setMode(result?.setup_required ? 'setup' : 'login')
      })
      .finally(() => setLoading(false))
  }, [navigate])

  if (!isSelfhostMode()) return <Navigate to="/" replace />

  const validateBasics = () => {
    if (!tenantName.trim()) {
      setError(t('selfhostSetup.workspaceRequired'))
      return false
    }
    if (admin.password.length < 8) {
      setError(t('selfhostSetup.passwordTooShort'))
      return false
    }
    return true
  }

  const goNext = () => {
    setError('')
    if (setupStep === 0 && !validateBasics()) return
    setSetupStep((value) => Math.min(value + 1, SETUP_STEPS.length - 1))
  }

  const goBack = () => {
    setError('')
    setSetupStep((value) => Math.max(value - 1, 0))
  }

  const skipAi = () => {
    setAiSkipped(true)
    goNext()
  }

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    if (mode === 'setup') {
      if (setupStep < SETUP_STEPS.length - 1) {
        goNext()
        return
      }
      if (!validateBasics()) return
    }
    setSaving(true)
    try {
      const result = mode === 'setup'
        ? await bootstrapSelfhost({
            tenant_name: tenantName,
            admin_username: admin.username,
            admin_password: admin.password,
            locale,
            installation_secrets: integrationPayload(integrations),
          })
        : await loginSelfhost(admin)
      if (!result?.token) throw new Error(t('selfhostSetup.error'))
      setToken(result.token)
      navigate('/workspaces/local/channels', { replace: true })
    } catch (err) {
      setError(err.message || t('selfhostSetup.error'))
    } finally {
      setSaving(false)
    }
  }

  const setIntegration = (category, key, value) => {
    setIntegrations((current) => ({
      ...current,
      [category]: { ...current[category], [key]: value },
    }))
  }

  const startGitSellAiGateway = async () => {
    setGitsellError('')
    setGitsellFlow({ status: 'starting' })
    try {
      const result = await startGitsellDeviceFlow({
        locale,
        instance_id: selfhostInstanceId(),
        instance_label: tenantName || 'Postbridge Self-host',
      })
      if (!result?.device_code || !result?.verification_uri_complete) {
        throw new Error(t('selfhostSetup.gitsell.error'))
      }
      setGitsellFlow({
        status: 'pending',
        device_code: result.device_code,
        user_code: result.user_code,
        verification_uri_complete: result.verification_uri_complete,
        interval: Number(result.interval || 3),
        ai_gateway: result.ai_gateway || {},
      })
      window.open(result.verification_uri_complete, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setGitsellFlow(null)
      setGitsellError(err.message || t('selfhostSetup.gitsell.error'))
    }
  }

  useEffect(() => {
    if (gitsellFlow?.status !== 'pending' || !gitsellFlow.device_code) return undefined
    let cancelled = false
    const intervalMs = Math.max(3, Number(gitsellFlow.interval || 3)) * 1000
    const timer = window.setInterval(async () => {
      try {
        const result = await pollGitsellDeviceFlow({
          locale,
          device_code: gitsellFlow.device_code,
        })
        if (cancelled || !result) return
        if (result.status === 'approved' && result.ai_gateway?.api_key) {
          setAiSkipped(false)
          setIntegration('ai_gateway', 'base_url', result.ai_gateway.base_url || gitsellFlow.ai_gateway?.base_url || '')
          setIntegration('ai_gateway', 'api_key', result.ai_gateway.api_key)
          setIntegration('ai_gateway', 'default_model', result.ai_gateway.default_model || gitsellFlow.ai_gateway?.default_model || 'gpt-5.4-mini')
          setIntegration('ai_gateway', 'image_model', result.ai_gateway.image_model || gitsellFlow.ai_gateway?.image_model || 'gpt-image-2')
          setIntegration('ai_gateway', 'image_size', result.ai_gateway.image_size || gitsellFlow.ai_gateway?.image_size || '1536x1024')
          setGitsellFlow({ ...gitsellFlow, status: 'approved' })
          window.clearInterval(timer)
        } else if (result.status === 'expired_token' || result.status === 'access_denied') {
          setGitsellFlow({ ...gitsellFlow, status: result.status })
          window.clearInterval(timer)
        } else if (result.interval && result.interval !== gitsellFlow.interval) {
          setGitsellFlow((current) => current ? { ...current, interval: result.interval } : current)
        }
      } catch (err) {
        if (!cancelled) setGitsellError(err.message || t('selfhostSetup.gitsell.error'))
      }
    }, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [gitsellFlow, locale, t])

  if (loading) {
    return <main className="container" style={{ paddingTop: '4rem' }}><p className="muted">{t('common.loading')}</p></main>
  }

  const renderLanguageBadges = () => mode === 'setup' && !isLocaleLocked && supportedLocales.length > 1 && (
    <div
      aria-label={t('selfhostSetup.locale')}
      style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginBottom: '1rem' }}
    >
      {supportedLocales.map((item) => (
        <button
          key={item.code}
          type="button"
          className={`btn btn-small ${locale === item.code ? '' : 'btn-secondary'}`}
          onClick={() => setLocale(item.code)}
          aria-pressed={locale === item.code}
          title={item.label}
        >
          {item.shortLabel}
        </button>
      ))}
    </div>
  )

  const renderSteps = () => mode === 'setup' && (
    <ol
      aria-label={t('selfhostSetup.steps.aria')}
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
        gap: '0.5rem',
        listStyle: 'none',
        padding: 0,
        margin: '0 0 1.25rem',
      }}
    >
      {SETUP_STEPS.map((step, index) => (
        <li
          key={step}
          style={{
            border: '1px solid var(--border-color)',
            borderRadius: '8px',
            padding: '0.65rem 0.75rem',
            background: index === setupStep ? 'rgba(59, 130, 246, 0.14)' : 'transparent',
            color: index <= setupStep ? 'var(--text-color)' : 'var(--muted-color)',
            fontWeight: index === setupStep ? 700 : 500,
          }}
        >
          <span className="muted" style={{ display: 'block', fontSize: '0.75rem' }}>{t('selfhostSetup.step', { current: index + 1, total: SETUP_STEPS.length })}</span>
          {t(`selfhostSetup.steps.${step}`)}
        </li>
      ))}
    </ol>
  )

  const renderBasics = () => (
    <section style={{ display: 'grid', gap: '1rem' }}>
      <div className="form-group">
        <label htmlFor="tenant-name">{t('selfhostSetup.workspace')}</label>
        <input id="tenant-name" className="form-control" value={tenantName} onChange={(e) => setTenantName(e.target.value)} required />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label htmlFor="admin-username">{t('selfhostSetup.username')}</label>
          <input id="admin-username" className="form-control" value={admin.username} onChange={(e) => setAdmin((v) => ({ ...v, username: e.target.value }))} autoComplete="username" required />
        </div>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label htmlFor="admin-password">{t('selfhostSetup.password')}</label>
          <input id="admin-password" className="form-control" type="password" value={admin.password} onChange={(e) => setAdmin((v) => ({ ...v, password: e.target.value }))} autoComplete="new-password" minLength={8} required />
          <p className="muted post-editor-hint">{t('selfhostSetup.passwordHint')}</p>
        </div>
      </div>
    </section>
  )

  const renderAiGateway = () => {
    const group = INTEGRATION_GROUPS.find((item) => item.category === 'ai_gateway')
    return (
      <section style={{ display: 'grid', gap: '1rem' }}>
        <div>
          <h2 className="h-small" style={{ marginTop: 0 }}>{t('selfhostSetup.aiStep.title')}</h2>
          <p className="muted">{t('selfhostSetup.aiStep.text')}</p>
          {aiSkipped && <p className="muted post-editor-hint">{t('selfhostSetup.aiStep.skipped')}</p>}
        </div>
        <button
          type="button"
          className="btn"
          onClick={startGitSellAiGateway}
          disabled={gitsellFlow?.status === 'starting' || gitsellFlow?.status === 'pending'}
        >
          {gitsellFlow?.status === 'starting'
            ? t('selfhostSetup.gitsell.starting')
            : gitsellFlow?.status === 'pending'
              ? t('selfhostSetup.gitsell.waiting')
              : gitsellFlow?.status === 'approved'
                ? t('selfhostSetup.gitsell.connected')
                : t('selfhostSetup.gitsell.connect')}
        </button>
        {gitsellFlow?.status === 'pending' && (
          <p className="muted post-editor-hint">
            {t('selfhostSetup.gitsell.pending', { code: gitsellFlow.user_code })}
          </p>
        )}
        {gitsellFlow?.status === 'access_denied' && (
          <p className="error">{t('selfhostSetup.gitsell.denied')}</p>
        )}
        {gitsellFlow?.status === 'expired_token' && (
          <p className="error">{t('selfhostSetup.gitsell.expired')}</p>
        )}
        {gitsellError && <p className="error">{gitsellError}</p>}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.75rem' }}>
          <button
            type="button"
            onClick={() => setManualAiOpen((value) => !value)}
            style={{
              fontSize: '0.85rem',
              padding: 0,
              border: 0,
              background: 'transparent',
              color: 'var(--accent)',
              cursor: 'pointer',
            }}
          >
            {manualAiOpen ? t('selfhostSetup.manual.hide') : t('selfhostSetup.manual.show')}
          </button>
          <button
            type="button"
            onClick={skipAi}
            style={{
              fontSize: '0.85rem',
              padding: 0,
              border: 0,
              background: 'transparent',
              color: 'var(--muted-color)',
              cursor: 'pointer',
            }}
          >
            {t('selfhostSetup.skipAi')}
          </button>
        </div>
        {manualAiOpen && group && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem' }}>
            {group.fields.map(([name, labelKey, type, placeholder]) => (
              <div className="form-group" style={{ marginBottom: 0 }} key={name}>
                <label htmlFor={`setup-${group.category}-${name}`}>{t(labelKey)}</label>
                <input
                  id={`setup-${group.category}-${name}`}
                  className="form-control"
                  type={type}
                  placeholder={placeholder}
                  value={integrations[group.category]?.[name] || ''}
                  onChange={(e) => {
                    setAiSkipped(false)
                    setIntegration(group.category, name, e.target.value)
                  }}
                  autoComplete="off"
                />
              </div>
            ))}
          </div>
        )}
      </section>
    )
  }

  const renderOptionalIntegrations = () => (
    <section style={{ display: 'grid', gap: '1rem' }}>
      <div>
        <h2 className="h-small" style={{ marginTop: 0 }}>{t('selfhostSetup.optionalStep.title')}</h2>
        <p className="muted">{t('selfhostSetup.optionalStep.text')}</p>
      </div>
      {INTEGRATION_GROUPS.filter((group) => group.category !== 'ai_gateway').map((group) => (
        <section key={group.category} style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
          <h3 className="h-small" style={{ marginTop: 0 }}>{t(group.titleKey)}</h3>
          <p className="muted post-editor-hint">
            {group.category === 'media_storage' ? t('selfhostSetup.mediaFallback') : t(group.textKey)}
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem' }}>
            {group.fields.map(([name, labelKey, type, placeholder]) => (
              <div className="form-group" style={{ marginBottom: 0 }} key={name}>
                <label htmlFor={`setup-${group.category}-${name}`}>{t(labelKey)}</label>
                <input
                  id={`setup-${group.category}-${name}`}
                  className="form-control"
                  type={type}
                  placeholder={placeholder}
                  value={integrations[group.category]?.[name] || ''}
                  onChange={(e) => setIntegration(group.category, name, e.target.value)}
                  autoComplete="off"
                />
              </div>
            ))}
          </div>
        </section>
      ))}
    </section>
  )

  const renderSetupStep = () => {
    if (setupStep === 0) return renderBasics()
    if (setupStep === 1) return renderAiGateway()
    return renderOptionalIntegrations()
  }

  return (
    <main className="container" style={{ paddingTop: '4rem', maxWidth: '52rem' }}>
      <div className="card">
        {renderLanguageBadges()}
        <h1 style={{ marginTop: 0 }}>{mode === 'setup' ? t('selfhostSetup.title') : t('selfhostSetup.loginTitle')}</h1>
        <p className="muted">{mode === 'setup' ? t('selfhostSetup.text') : t('selfhostSetup.loginText')}</p>
        {renderSteps()}
        <form onSubmit={submit} style={{ display: 'grid', gap: '1rem' }}>
          {mode === 'setup' ? renderSetupStep() : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="admin-username">{t('selfhostSetup.username')}</label>
                <input id="admin-username" className="form-control" value={admin.username} onChange={(e) => setAdmin((v) => ({ ...v, username: e.target.value }))} autoComplete="username" required />
              </div>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="admin-password">{t('selfhostSetup.password')}</label>
                <input id="admin-password" className="form-control" type="password" value={admin.password} onChange={(e) => setAdmin((v) => ({ ...v, password: e.target.value }))} autoComplete="current-password" minLength={1} required />
              </div>
            </div>
          )}
          {error && <p className="error">{error}</p>}
          {mode === 'setup' ? (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem' }}>
              <button type="button" className="btn btn-secondary" onClick={goBack} disabled={saving || setupStep === 0}>
                {t('common.back')}
              </button>
              <button type="submit" className="btn" disabled={saving}>
                {saving ? t('common.savingShort') : setupStep === SETUP_STEPS.length - 1 ? t('selfhostSetup.finish') : t('common.next')}
              </button>
            </div>
          ) : (
            <button type="submit" className="btn" disabled={saving}>
              {saving ? t('common.savingShort') : t('common.login')}
            </button>
          )}
        </form>
      </div>
    </main>
  )
}
