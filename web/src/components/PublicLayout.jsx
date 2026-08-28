import { Link } from 'react-router-dom'
import { isSelfhostMode } from '../adapters/runtime'
import { BILLING_SUPPORT_EMAIL } from '../billingSupport'
import { listPublicCaseLandings } from '../caseLandings'
import { LanguageSelect, useI18n } from '../i18n'

const brandMarkSrc = `${import.meta.env.BASE_URL}postbridge-mark.svg`

const publicCaseLinks = listPublicCaseLandings().map((landing) => ({
  to: `/cases/${landing.slug}`,
  labelKey: landing.navLabelKey,
}))
const showIoDirectory = publicCaseLinks.some((item) =>
  item.to === '/cases/multi-platform-publishing' || item.to === '/cases/chatgpt-social-publishing',
)

export default function PublicLayout({ children, compact = false }) {
  const { t } = useI18n()
  const selfhost = isSelfhostMode()

  return (
    <div className="public-shell">
      <header className="public-header">
        <div className="container public-header-inner">
          <Link to="/" className="brand">
            <img src={brandMarkSrc} alt="" className="brand-mark" width="28" height="28" />
            Postbridge
          </Link>
          <nav className="public-nav" aria-label={t('public.nav.aria')}>
            {!selfhost && <Link to="/news">{t('common.news')}</Link>}
            {showIoDirectory && <a href="/platforms">Platforms</a>}
            <details className="public-nav-menu">
              <summary>{t('common.cases')}</summary>
              <div className="public-nav-menu-list">
                {showIoDirectory && <a href="/cases">All use cases</a>}
                {publicCaseLinks.map((item) => (
                  <Link to={item.to} key={item.to}>
                    {t(item.labelKey)}
                  </Link>
                ))}
              </div>
            </details>
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
            {showIoDirectory ? <a href="/cases">{t('common.cases')}</a> : <Link to="/cases/telegram-to-max">{t('common.cases')}</Link>}
            {showIoDirectory && <a href="/platforms">Platforms</a>}
            {showIoDirectory && <a href="/docs/mcp">MCP</a>}
            <Link to="/agents/help">{t('common.faq')}</Link>
            {!selfhost && <Link to="/pricing">{t('common.pricing')}</Link>}
            <Link to="/privacy">Privacy</Link>
            <Link to="/terms">Terms</Link>
            <Link to="/login">{t('common.login')}</Link>
            <a href={`mailto:${BILLING_SUPPORT_EMAIL}`}>{t('common.support')}</a>
          </div>
        </div>
      </footer>
    </div>
  )
}
