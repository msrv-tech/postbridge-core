import { useI18n } from '../i18n'

const brandMarkSrc = `${import.meta.env.BASE_URL}postbridge-mark.svg`

export default function LoadingSkeleton() {
  const { t } = useI18n()

  return (
    <div className="public-shell">
      <header className="public-header">
        <div className="container public-header-inner">
          <span className="brand">
            <img src={brandMarkSrc} alt="" className="brand-mark" width="28" height="28" />
            Postbridge
          </span>
          <div className="public-nav" style={{ opacity: 0.6 }}>
            <span>{t('loading.nav.pricing')}</span>
            <span>{t('loading.nav.login')}</span>
          </div>
        </div>
      </header>
      <main className="public-main">
        <div className="container">
          <div className="loading-skeleton">
            <div className="loading-skeleton-line loading-skeleton-title" />
            <div className="loading-skeleton-line" />
            <div className="loading-skeleton-line" />
            <div className="loading-skeleton-line loading-skeleton-short" />
          </div>
        </div>
      </main>
    </div>
  )
}
