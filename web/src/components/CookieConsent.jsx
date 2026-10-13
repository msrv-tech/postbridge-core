import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { COOKIE_CONSENT_KEY, readCookieConsent, writeCookieConsent } from '../cookieConsent'
import { disableMetrika, enableMetrika, METRIKA_COUNTER_ID } from '../metrika'
import { useI18n } from '../i18n'

export default function CookieConsent() {
  const { t } = useI18n()
  const location = useLocation()
  const navigate = useNavigate()
  const [visible, setVisible] = useState(() => readCookieConsent() === null || new URLSearchParams(location.search).get('cookie-settings') === '1')
  const [error, setError] = useState(false)

  useEffect(() => {
    function synchronize(event) {
      if (event.key !== null && event.key !== COOKIE_CONSENT_KEY) return
      const consent = readCookieConsent()
      setVisible(consent === null)
      if (consent === 'accepted') enableMetrika()
      else disableMetrika()
    }
    window.addEventListener('storage', synchronize)
    return () => {
      window.removeEventListener('storage', synchronize)
    }
  }, [])

  useEffect(() => {
    if (new URLSearchParams(location.search).get('cookie-settings') === '1') setVisible(true)
  }, [location.search])

  function choose(value) {
    const saved = writeCookieConsent(value)
    if (!saved) {
      if (value === 'rejected') disableMetrika({ reload: false })
      setError(true)
      return
    }
    const params = new URLSearchParams(location.search)
    if (params.has('cookie-settings')) {
      params.delete('cookie-settings')
      navigate({ pathname: location.pathname, search: params.toString(), hash: location.hash }, { replace: true })
    }
    if (value === 'rejected') disableMetrika()
    if (value === 'accepted') enableMetrika()
    setError(false)
    setVisible(false)
  }

  if (!visible || !Number.isFinite(METRIKA_COUNTER_ID) || METRIKA_COUNTER_ID <= 0) return null
  return (
    <section className="cookie-banner" aria-label={t('cookies.title')}>
      <button type="button" className="cookie-banner-close" aria-label={t('cookies.close')} title={t('cookies.close')} onClick={() => choose('rejected')}>
        <span aria-hidden="true">×</span>
      </button>
      <div className="cookie-banner-text">
        <strong>{t('cookies.title')}</strong>
        <p>{t('cookies.text')} <a href="/privacy">{t('cookies.policy')}</a></p>
        {error && <p role="alert">{t('cookies.storageError')}</p>}
      </div>
      <div className="cookie-banner-actions">
        <button type="button" className="btn btn-secondary" onClick={() => choose('accepted')}>{t('cookies.accept')}</button>
      </div>
    </section>
  )
}
