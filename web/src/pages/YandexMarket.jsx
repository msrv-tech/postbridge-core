import { useEffect, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { isSelfhostMode } from '../adapters/runtime'
import { getPublicMarket } from '../branding'
import { useI18n } from '../i18n'
import './YandexMarket.css'

const key = (name) => `marketLanding.${name}`
const exampleModes = ['post', 'short', 'video']

function usePageMetadata(t) {
  useEffect(() => {
    const previousTitle = document.title
    const meta = document.querySelector('meta[name="description"]')
    const previousDescription = meta?.content
    const canonical = document.querySelector('link[rel="canonical"]')
    const previousCanonical = canonical?.href
    document.title = `${t(key('title'))} | Postbridge`
    meta?.setAttribute('content', t(key('subtitle')))
    canonical?.setAttribute('href', `${window.location.origin}/integrations/yandex-market`)
    return () => {
      document.title = previousTitle
      if (previousDescription != null) meta?.setAttribute('content', previousDescription)
      if (previousCanonical != null) canonical?.setAttribute('href', previousCanonical)
    }
  }, [t])
}

export default function YandexMarket() {
  const { t } = useI18n()
  const [mode, setMode] = useState('post')
  usePageMetadata(t)
  if (isSelfhostMode() || getPublicMarket() !== 'ru') return <Navigate to="/" replace />
  const supportHref = `mailto:support@postbridge.ru?subject=${encodeURIComponent(t(key('pilotSubject')))}`
  const bonusHref = `mailto:support@postbridge.ru?subject=${encodeURIComponent(t(key('bonusSubject')))}&body=${encodeURIComponent(t(key('bonusMail')))}`
  return (
    <PublicLayout className="market-landing">
      <section className="section market-hero">
        <div className="container market-hero-grid">
          <div>
            <span className="eyebrow">{t(key('eyebrow'))}</span>
            <h1 className="hero-title">{t(key('title'))}</h1>
            <p className="hero-text">{t(key('subtitle'))}</p>
            <div className="hero-actions">
              <a className="btn" href={supportHref}>{t(key('pilotCta'))}</a>
              <a className="btn btn-secondary" href="#example">{t(key('exampleCta'))}</a>
            </div>
            <p className="market-status"><span aria-hidden="true" />{t(key('status'))}</p>
          </div>
          <aside className="market-chat" id="example" aria-label={t(key('demoAria'))}>
            <header><img src="/postbridge-mark.svg" alt="" width="34" height="34" /><div><strong>{t(key('chatTitle'))}</strong><small>{t(key('demoLabel'))}</small></div></header>
            <div className="market-demo-tabs" role="group" aria-label={t(key('demoTabs'))}>
              {exampleModes.map((item) => <button type="button" key={item} aria-pressed={mode === item}
                className={mode === item ? 'is-active' : ''} onClick={() => setMode(item)}>{t(key(`demo.${item}.label`))}</button>)}
            </div>
            <div className="market-chat-body" aria-live="polite">
              <p className="market-message-user">{t(key(`demo.${mode}.request`))}</p>
              <div className="market-product"><span aria-hidden="true">☀</span><div><strong>{t(key('sampleProduct'))}</strong><small>{t(key('sampleFacts'))}</small></div></div>
              <div className="market-message-assistant"><span>{t(key('sampleResult'))}</span><p>{t(key(`demo.${mode}.result`))}</p></div>
              <small className="market-demo-disclosure">{t(key('demoDisclosure'))}</small>
            </div>
          </aside>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="section-heading"><span className="eyebrow">{t(key('workflowEyebrow'))}</span><h2>{t(key('workflowTitle'))}</h2></div>
          <ol className="market-steps">{[1, 2, 3, 4].map((i) => <li key={i}><span>{String(i).padStart(2, '0')}</span><h3>{t(key(`step.${i}.title`))}</h3><p>{t(key(`step.${i}.body`))}</p></li>)}</ol>
        </div>
      </section>

      <section className="section market-shared">
        <div className="container market-info-grid">
          <div><span className="eyebrow">Postbridge</span><h2>{t(key('sharedTitle'))}</h2><p>{t(key('sharedBody'))}</p><Link className="btn btn-secondary" to="/pricing">{t(key('pricingCta'))}</Link></div>
          <ul>{[1, 2, 3].map((i) => <li key={i}><strong>{t(key(`shared.${i}.title`))}</strong><p>{t(key(`shared.${i}.body`))}</p></li>)}</ul>
        </div>
      </section>

      <section className="section" id="guide">
        <div className="container">
          <div className="section-heading"><span className="eyebrow">{t(key('guideEyebrow'))}</span><h2>{t(key('guideTitle'))}</h2><p>{t(key('guideIntro'))}</p></div>
          <div className="market-guide-grid">{['now', 'pilot'].map((stage) => <article key={stage}><span className={`market-stage market-stage-${stage}`}>{t(key(`stage.${stage}`))}</span><h3>{t(key(`guide.${stage}.title`))}</h3><p>{t(key(`guide.${stage}.body`))}</p></article>)}</div>
          <details className="market-details"><summary>{t(key('keyTitle'))}</summary><p>{t(key('keyBody'))}</p><a href="https://yandex.ru/dev/market/partner-api/doc/ru/concepts/api-key" target="_blank" rel="noreferrer">{t(key('keyDocs'))} ↗</a></details>
          <details className="market-details"><summary>{t(key('dataTitle'))}</summary><p>{t(key('dataBody'))}</p><div className="market-policy-links"><Link to="/privacy">{t('public.nav.privacy')}</Link><Link to="/data-deletion">{t(key('deletion'))}</Link></div></details>
        </div>
      </section>

      <section className="section" id="seller-bonus">
        <div className="container"><div className="market-bonus"><div><span className="eyebrow">{t(key('bonusEyebrow'))}</span><h2>{t(key('bonusTitle'))}</h2><p>{t(key('bonusBody'))}</p><a className="btn" href={bonusHref}>{t(key('bonusCta'))}</a></div><div className="market-bonus-terms"><strong>{t(key('bonusTermsTitle'))}</strong><ul>{[1, 2, 3, 4, 5].map((i) => <li key={i}>{t(key(`bonusTerm.${i}`))}</li>)}</ul></div></div></div>
      </section>

      <section className="section"><div className="container market-contact"><div><h2>{t(key('contactTitle'))}</h2><p>{t(key('contactBody'))}</p></div><a href="mailto:support@postbridge.ru">support@postbridge.ru ↗</a></div></section>
    </PublicLayout>
  )
}
