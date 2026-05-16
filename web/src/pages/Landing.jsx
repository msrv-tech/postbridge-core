import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { listAuthProviders, requestMagicLink, verifyMagicLink } from '../adapters/authFlows'
import { setToken } from '../adapters/sessionToken'
import PublicLayout from '../components/PublicLayout'
import TelegramDeepLinkField from '../components/TelegramDeepLinkField'
import { fetchTelegramWebLinkStatus, startTelegramWebLinkSession } from '../telegramWebLinkFlow'
import { reachMetrikaGoal } from '../metrika'
import { useI18n } from '../i18n'

function AuthProviderLogo({ provider }) {
  if (provider === 'telegram') {
    return (
      <span className="auth-provider-logo auth-provider-logo-telegram" aria-hidden="true">
        <svg viewBox="0 0 24 24" focusable="false">
          <path d="M21.6 4.2 18.4 19c-.2 1-.8 1.2-1.6.8l-4.5-3.3-2.2 2.1c-.2.2-.4.4-.9.4l.3-4.6 8.4-7.6c.4-.3-.1-.5-.5-.2L7 13.1 2.6 11.7c-.9-.3-.9-.9.2-1.3L20.2 3.7c.8-.3 1.5.2 1.4.5Z" />
        </svg>
      </span>
    )
  }
  if (provider === 'google') {
    return (
      <span className="auth-provider-logo auth-provider-logo-google" aria-hidden="true">
        <svg viewBox="0 0 48 48" focusable="false">
          <path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 8 3l5.7-5.7C34 6.1 29.3 4 24 4 13 4 4 13 4 24s9 20 20 20 20-9 20-20c0-1.3-.1-2.6-.4-3.9Z" />
          <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 8 3l5.7-5.7C34 6.1 29.3 4 24 4c-7.7 0-14.3 4.3-17.7 10.7Z" />
          <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44Z" />
          <path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C36.9 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.9Z" />
        </svg>
      </span>
    )
  }
  if (provider === 'vk') {
    return (
      <span className="auth-provider-logo auth-provider-logo-vk" aria-hidden="true">
        VK
      </span>
    )
  }
  if (provider === 'linkedin') {
    return (
      <span className="auth-provider-logo auth-provider-logo-linkedin" aria-hidden="true">
        in
      </span>
    )
  }
  return null
}

export default function Landing() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [codeSent, setCodeSent] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [tgDeepLink, setTgDeepLink] = useState(null)
  const [tgSessionToken, setTgSessionToken] = useState(null)
  const [tgLinkLoading, setTgLinkLoading] = useState(false)
  const [tgHint, setTgHint] = useState('')
  const [authProviders, setAuthProviders] = useState(null)

  const providerEnabled = (providerId) => {
    if (!Array.isArray(authProviders)) return ['telegram', 'vk', 'email'].includes(providerId)
    return authProviders.some((provider) => provider?.id === providerId)
  }

  const beginTelegramLogin = async () => {
    setError('')
    setTgHint('')
    setTgLinkLoading(true)
    reachMetrikaGoal('auth_started', { method: 'telegram' })
    try {
      const res = await startTelegramWebLinkSession()
      setTgDeepLink(res.deep_link)
      setTgSessionToken(res.session_token)
      setTgHint(t('login.telegram.hint'))
    } catch (e) {
      setError(e.message)
      setTgDeepLink(null)
      setTgSessionToken(null)
    } finally {
      setTgLinkLoading(false)
    }
  }

  const cancelTelegramLogin = () => {
    setTgDeepLink(null)
    setTgSessionToken(null)
    setTgHint('')
  }

  useEffect(() => {
    if (!tgSessionToken) return undefined
    const id = setInterval(async () => {
      try {
        const s = await fetchTelegramWebLinkStatus(tgSessionToken)
        if (s.status === 'done' && s.token) {
          setToken(s.token)
          reachMetrikaGoal('auth_completed', { method: 'telegram' })
          navigate('/')
          return
        }
        if (s.status === 'failed') {
          setError(s.message || t('login.telegram.failed'))
          cancelTelegramLogin()
        }
        if (s.status === 'expired') {
          setError(t('login.telegram.expired'))
          cancelTelegramLogin()
        }
      } catch (e) {
        setError(e.message)
      }
    }, 2000)
    return () => clearInterval(id)
  }, [tgSessionToken, navigate, t])

  useEffect(() => {
    let cancelled = false
    listAuthProviders()
      .then((data) => {
        if (!cancelled) {
          setAuthProviders(Array.isArray(data?.providers) ? data.providers : [])
        }
      })
      .catch(() => {
        if (!cancelled) setAuthProviders(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleRequestCode = async (e) => {
    e.preventDefault()
    setError('')
    setSuccess('')
    setLoading(true)
    reachMetrikaGoal('auth_started', { method: 'email' })
    try {
      const res = await requestMagicLink({ email })
      setCodeSent(true)
      setSuccess(
        res.code
          ? t('login.email.successWithCode', { code: res.code })
          : (res.message || t('login.email.checkInbox')),
      )
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleVerifyCode = async (e) => {
    e.preventDefault()
    setError('')
    setVerifying(true)
    try {
      const res = await verifyMagicLink({ code: code.trim().toUpperCase() })
      setToken(res.token)
      reachMetrikaGoal('auth_completed', { method: 'email' })
      navigate('/')
    } catch (e) {
      setError(e.message)
      setVerifying(false)
    }
  }

  useEffect(() => {
    const vkErr = searchParams.get('vk_oauth_error')
    const googleErr = searchParams.get('google_oauth_error')
    const linkedinErr = searchParams.get('linkedin_oauth_error')
    if (vkErr) {
      setError(t('login.vk.error', { error: vkErr }))
      setSearchParams({}, { replace: true })
    } else if (googleErr) {
      setError(t('login.google.error', { error: googleErr }))
      setSearchParams({}, { replace: true })
    } else if (linkedinErr) {
      setError(t('login.linkedin.error', { error: linkedinErr }))
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams, t])

  return (
    <PublicLayout compact>
      <section className="section">
        <div className="container auth-layout auth-layout-single">
          <div className="card auth-card">
            <h2>{t('login.title')}</h2>
            {providerEnabled('telegram') && (
              <div className="telegram-web-link-block" style={{ marginBottom: '1rem' }}>
                {!tgDeepLink ? (
                  <button
                    type="button"
                    className="btn btn-block auth-provider-button"
                    onClick={beginTelegramLogin}
                    disabled={tgLinkLoading}
                  >
                    <AuthProviderLogo provider="telegram" />
                    {tgLinkLoading ? t('login.telegram.creating') : t('login.telegram.start')}
                  </button>
                ) : (
                  <>
                    <p className="text-small" style={{ marginBottom: '0.5rem' }}>{tgHint}</p>
                    <TelegramDeepLinkField url={tgDeepLink} />
                    <a href={tgDeepLink} target="_blank" rel="noopener noreferrer" className="btn btn-block">
                      {t('login.telegram.open')}
                    </a>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      style={{ marginTop: '0.5rem', width: '100%' }}
                      onClick={cancelTelegramLogin}
                    >
                      {t('common.cancel')}
                    </button>
                  </>
                )}
              </div>
            )}
            {providerEnabled('google') && (
              <a
                href="/auth/google/start"
                className="btn btn-outline btn-block auth-provider-button auth-provider-button-spaced"
                onClick={() => reachMetrikaGoal('auth_started', { method: 'google' })}
              >
                <AuthProviderLogo provider="google" />
                {t('login.google')}
              </a>
            )}
            {providerEnabled('linkedin') && (
              <a
                href="/auth/linkedin/start"
                className="btn btn-outline btn-block auth-provider-button auth-provider-button-spaced"
                onClick={() => reachMetrikaGoal('auth_started', { method: 'linkedin' })}
              >
                <AuthProviderLogo provider="linkedin" />
                {t('login.linkedin')}
              </a>
            )}
            {providerEnabled('vk') && (
              <a
                href="/auth/vk/start"
                className="btn btn-outline btn-block auth-provider-button"
                onClick={() => reachMetrikaGoal('auth_started', { method: 'vk' })}
              >
                <AuthProviderLogo provider="vk" />
                {t('login.vk')}
              </a>
            )}
            {providerEnabled('email') && (
              <>
                <div className="auth-divider">{t('login.email.divider')}</div>
                {!codeSent ? (
                  <form onSubmit={handleRequestCode}>
                    <div className="form-group">
                      <label htmlFor="email">{t('common.email')}</label>
                      <input
                        id="email"
                        type="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="you@example.com"
                        required
                        style={{ width: '100%' }}
                      />
                    </div>
                    {error && <p className="error">{error}</p>}
                    {success && <p className="success">{success}</p>}
                    <button type="submit" className="btn btn-block" disabled={loading}>
                      {loading ? t('login.email.sending') : t('login.email.requestCode')}
                    </button>
                  </form>
                ) : (
                  <form onSubmit={handleVerifyCode}>
                    <div className="form-group">
                      <label htmlFor="code">{t('login.email.codeLabel')}</label>
                      <input
                        id="code"
                        type="text"
                        value={code}
                        onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 4))}
                        placeholder="1234"
                        maxLength={4}
                        autoComplete="one-time-code"
                        inputMode="numeric"
                        pattern="[0-9]*"
                        style={{ width: '100%', letterSpacing: '0.3em', textAlign: 'center' }}
                      />
                    </div>
                    {error && <p className="error">{error}</p>}
                    {success && <p className="success">{success}</p>}
                    <button type="submit" className="btn btn-block" disabled={verifying || !code.trim()}>
                      {verifying ? t('login.email.verifying') : t('common.login')}
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      style={{ marginTop: '0.5rem', width: '100%' }}
                      onClick={() => { setCodeSent(false); setCode(''); setSuccess(''); setError(''); }}
                    >
                      {t('login.email.useAnother')}
                    </button>
                  </form>
                )}
              </>
            )}
            {error && !providerEnabled('email') && <p className="error">{error}</p>}
          </div>
        </div>
      </section>
    </PublicLayout>
  )
}
