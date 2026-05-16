import { Link } from 'react-router-dom'
import { isSelfhostMode } from '../adapters/runtime'
import { BILLING_SUPPORT_EMAIL } from '../billingSupport'
import { LanguageSelect, useI18n } from '../i18n'

export default function PublicLayout({ children, compact = false }) {
  const { t } = useI18n()
  const selfhost = isSelfhostMode()

  return (
    <div className="public-shell">
      <header className="public-header">
        <div className="container public-header-inner">
          <Link to="/" className="brand">
            <img src="/postbridge-mark.svg" alt="" className="brand-mark" width="28" height="28" />
            Postbridge
          </Link>
          <nav className="public-nav" aria-label={t('public.nav.aria')}>
            {!selfhost && <Link to="/news">{t('common.news')}</Link>}
            <Link to="/cases/telegram-to-max">{t('common.cases')}</Link>
            <Link to="/agents/help">{t('common.faq')}</Link>
            {!selfhost && <Link to="/pricing">{t('common.pricing')}</Link>}
            <LanguageSelect compact />
            <Link to="/login" className="btn btn-secondary btn-small">
              {t('common.login')}
            </Link>
          </nav>
        </div>
      </header>

      <main className={compact ? 'public-main public-main-compact' : 'public-main'}>
        {children}
      </main>

      <footer className="public-footer">
        <div className="container public-footer-inner">
          <p>{t('public.footer.summary')}</p>
          <div className="public-footer-links">
            {!selfhost && <Link to="/news">{t('common.news')}</Link>}
            <Link to="/cases/telegram-to-max">{t('common.cases')}</Link>
            <Link to="/agents/help">{t('common.faq')}</Link>
            {!selfhost && <Link to="/pricing">{t('common.pricing')}</Link>}
            <Link to="/login">{t('common.login')}</Link>
            <a href={`mailto:${BILLING_SUPPORT_EMAIL}`}>{t('common.support')}</a>
          </div>
        </div>
      </footer>
    </div>
  )
}
