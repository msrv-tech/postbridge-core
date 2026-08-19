import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { listNews } from '../adapters/news'
import { useI18n } from '../i18n'

function getLandingRegion() {
  if (typeof window === 'undefined') return 'ru'
  const host = window.location.hostname.toLowerCase()
  return host === 'postbridge.io' || host === 'www.postbridge.io' ? 'io' : 'ru'
}

function getRegionalKey(region, key) {
  return region === 'io' ? `${key}.io` : key
}

function getSteps(t, region) {
  return [
  {
    title: t('home.steps.assign.title'),
    text: t('home.steps.assign.text'),
  },
  {
    title: t('home.steps.review.title'),
    text: t('home.steps.review.text'),
  },
  {
    title: t('home.steps.deliver.title'),
    text: t(getRegionalKey(region, 'home.steps.deliver.text')),
  },
]
}

function getAiPillars(t) {
  return [
  {
    title: t('home.ai.topicScout.title'),
    text: t('home.ai.topicScout.text'),
  },
  {
    title: t('home.ai.editor.title'),
    text: t('home.ai.editor.text'),
  },
  {
    title: t('home.ai.reviewQueue.title'),
    text: t('home.ai.reviewQueue.text'),
  },
]
}

function getFeatures(t, region) {
  return [
  {
    title: t('home.features.agentWorkflow.title'),
    text: t('home.features.agentWorkflow.text'),
  },
  {
    title: t('home.features.bridges.title'),
    text: t('home.features.bridges.text'),
  },
  {
    title: t('home.features.channels.title'),
    text: t(getRegionalKey(region, 'home.features.channels.text')),
  },
  {
    title: t('home.features.publicationControl.title'),
    text: t('home.features.publicationControl.text'),
  },
  {
    title: t('home.features.workspace.title'),
    text: t('home.features.workspace.text'),
  },
]
}

function getTrustItems(t) {
  return [
    t('home.trust.item.runs'),
    t('home.trust.item.reviewQueue'),
    t('home.trust.item.policies'),
  ]
}

function getAgentBridgeExamples(t, region) {
  if (region === 'io') {
    return [
      { from: 'Postbridge', to: t('platform.telegram'), fromKey: 'postbridge', toKey: 'telegram', label: t('home.bridge.agent.telegram') },
      { from: 'Postbridge', to: t('platform.linkedin'), fromKey: 'postbridge', toKey: 'linkedin', label: t('home.bridge.agent.linkedin') },
      { from: 'Postbridge', to: t('platform.x'), fromKey: 'postbridge', toKey: 'x', label: t('home.bridge.agent.x') },
      { from: 'Postbridge', to: 'RSS', fromKey: 'postbridge', toKey: 'rss', label: t('home.bridge.agent.rss') },
    ]
  }

  return [
    { from: 'Postbridge', to: t('platform.telegram'), fromKey: 'postbridge', toKey: 'telegram', label: t('home.bridge.agent.telegram') },
    { from: 'Postbridge', to: t('platform.max'), fromKey: 'postbridge', toKey: 'max', label: t('home.bridge.agent.max') },
    { from: 'Postbridge', to: t('platform.vk'), fromKey: 'postbridge', toKey: 'vk', label: t('home.bridge.agent.vk') },
    { from: 'Postbridge', to: 'RSS', fromKey: 'postbridge', toKey: 'rss', label: t('home.bridge.agent.rss') },
  ]
}

function getPlatformBridgeExamples(t, region) {
  if (region === 'io') {
    return [
      { from: t('platform.telegram'), to: t('platform.x'), fromKey: 'telegram', toKey: 'x', label: t('home.bridge.direct.telegramToX') },
      { from: 'RSS', to: t('platform.linkedin'), fromKey: 'rss', toKey: 'linkedin', label: t('home.bridge.direct.rssToLinkedin') },
      { from: 'RSS', to: t('platform.facebook'), fromKey: 'rss', toKey: 'facebook', label: t('home.bridge.direct.rssToFacebook') },
      { from: 'RSS', to: t('platform.instagram'), fromKey: 'rss', toKey: 'instagram', label: t('home.bridge.direct.rssToInstagram') },
    ]
  }

  return [
    { from: t('platform.telegram'), to: t('platform.max'), fromKey: 'telegram', toKey: 'max', label: t('home.bridge.direct.crosspost') },
    { from: t('platform.vk'), to: t('platform.max'), fromKey: 'vk', toKey: 'max', label: t('home.bridge.direct.groupToMax') },
    { from: t('platform.vk'), to: t('platform.vk'), fromKey: 'vk', toKey: 'vk', label: t('home.bridge.direct.vk') },
    { from: 'RSS', to: t('platform.max'), fromKey: 'rss', toKey: 'max', label: t('home.bridge.direct.blogToMax') },
  ]
}

function getFaqItems(t, region) {
  return [
  {
    question: t('home.faq.who.question'),
    answer: t('home.faq.who.answer'),
  },
  {
    question: t('home.faq.agents.question'),
    answer: t('home.faq.agents.answer'),
  },
  {
    question: t('home.faq.destinations.question'),
    answer: t(getRegionalKey(region, 'home.faq.destinations.answer')),
  },
]
}

function newsPreview(value, maxLen = 180) {
  if (!value) return ''
  const plain = String(value).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
  return plain.length > maxLen ? plain.slice(0, maxLen - 1).trim() + '…' : plain
}

function newsKey(item, index) {
  return item.slug || item.link || `${item.title}-${index}`
}

function formatNewsDate(value, locale, t) {
  if (!value) return t('common.noDate')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return t('common.noDate')
  return date.toLocaleDateString(locale === 'en' ? 'en-US' : 'ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

export default function Home() {
  const { locale, t } = useI18n()
  const landingRegion = getLandingRegion()
  const [newsItems, setNewsItems] = useState([])
  const [newsSourceUrl, setNewsSourceUrl] = useState('')
  const [newsLoading, setNewsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    listNews({ limit: 5 })
      .then((data) => {
        if (cancelled) return
        setNewsItems(Array.isArray(data?.items) ? data.items : [])
        setNewsSourceUrl(typeof data?.source_url === 'string' ? data.source_url : '')
      })
      .catch(() => {
        if (cancelled) return
        setNewsItems([])
        setNewsSourceUrl('')
      })
      .finally(() => {
        if (!cancelled) setNewsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const steps = getSteps(t, landingRegion)
  const aiPillars = getAiPillars(t)
  const features = getFeatures(t, landingRegion)
  const trustItems = getTrustItems(t)
  const agentBridgeExamples = getAgentBridgeExamples(t, landingRegion)
  const platformBridgeExamples = getPlatformBridgeExamples(t, landingRegion)
  const faqItems = getFaqItems(t, landingRegion)
  const platformNoteKey = getRegionalKey(landingRegion, 'home.features.platformNote')
  const heroTextKey = getRegionalKey(landingRegion, 'home.hero.text')

  return (
    <PublicLayout>
      <section className="hero hero-ai">
        <div className="hero-ai-glow" aria-hidden />
        <div className="container hero-grid">
          <div>
            <span className="eyebrow">{t('home.hero.eyebrow')}</span>
            <h1 className="hero-title">{t('home.hero.title')}</h1>
            <p className="hero-text">
              {t(heroTextKey)}
            </p>
            <div className="hero-actions">
              <Link to="/login" className="btn">
                {t('home.hero.startAgent')}
              </Link>
              <Link to="/pricing" className="btn btn-secondary">
                {t('common.pricing')}
              </Link>
            </div>
          </div>

          <div className="card hero-card hero-card-ai agent-hero-card agent-wow-card">
            <div className="hero-card-row">
              <span className="badge badge-running">{t('home.heroCard.badge')}</span>
            </div>
            <div className="hero-metric">
              <strong>{t('home.heroCard.promptQuote')}</strong>
              <span>{t('home.heroCard.promptHint')}</span>
            </div>
            <div className="agent-wow-terminal" aria-label={t('home.heroCard.aria')}>
              <div className="agent-wow-prompt">
                <span>{t('home.heroCard.task')}</span>
                <p>{t('home.heroCard.taskText')}</p>
              </div>
              <div className="agent-wow-status">
                <span className="agent-wow-dot" aria-hidden />
                {t('home.heroCard.status')}
              </div>
              <div className="agent-wow-line" aria-hidden />
            </div>
            <div className="agent-flow-preview agent-wow-steps" aria-label={t('home.heroCard.stepsAria')}>
              <div className="agent-flow-step">
                <span>01</span>
                <strong>{t('home.heroCard.step1.title')}</strong>
                <p>{t('home.heroCard.step1.text')}</p>
              </div>
              <div className="agent-flow-step">
                <span>02</span>
                <strong>{t('home.heroCard.step2.title')}</strong>
                <p>{t('home.heroCard.step2.text')}</p>
              </div>
              <div className="agent-flow-step">
                <span>03</span>
                <strong>{t('home.heroCard.step3.title')}</strong>
                <p>{t('home.heroCard.step3.text')}</p>
              </div>
            </div>
            <div className="agent-wow-result">
              <div>
                <span>{t('home.heroCard.doneAt')}</span>
                <strong>{t('home.heroCard.result')}</strong>
              </div>
              <Link to="/login" className="btn btn-secondary btn-small">
                {t('home.heroCard.open')}
              </Link>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.how.eyebrow')}</span>
            <h2>{t('home.how.title')}</h2>
          </div>
          <div className="feature-grid feature-grid-steps">
            {steps.map((step, index) => (
              <article className="card feature-card" key={step.title}>
                <span className="step-number">0{index + 1}</span>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section section-muted">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.bridge.eyebrow')}</span>
            <h2>{t('home.bridge.title')}</h2>
            <p className="section-copy bridge-intro">
              {t('home.bridge.text')}
            </p>
          </div>
          <div className="bridge-scenarios">
            <div className="bridge-scenario">
              <div className="bridge-scenario-head">
                <h3>{t('home.bridge.agent.title')}</h3>
                <p>{t('home.bridge.agent.text')}</p>
              </div>
              <div className="bridge-diagram">
                {agentBridgeExamples.map((bridge, i) => (
                  <div key={i} className="bridge-flow bridge-flow-agent">
                    <span className={`bridge-node bridge-node-${bridge.fromKey}`}>{bridge.from}</span>
                    <span className="bridge-arrow" aria-hidden>
                      →
                    </span>
                    <span className={`bridge-node bridge-node-${bridge.toKey}`}>{bridge.to}</span>
                    {bridge.label && <span className="bridge-label">{bridge.label}</span>}
                  </div>
                ))}
              </div>
            </div>
            <div className="bridge-scenario">
              <div className="bridge-scenario-head">
                <h3>{t('home.bridge.direct.title')}</h3>
                <p>{t('home.bridge.direct.text')}</p>
              </div>
              <div className="bridge-diagram">
                {platformBridgeExamples.map((bridge, i) => (
                  <div key={i} className="bridge-flow">
                    <span className={`bridge-node bridge-node-${bridge.fromKey}`}>{bridge.from}</span>
                    <span className="bridge-arrow" aria-hidden>
                      →
                    </span>
                    <span className={`bridge-node bridge-node-${bridge.toKey}`}>{bridge.to}</span>
                    {bridge.label && <span className="bridge-label">{bridge.label}</span>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="section section-ai">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.ai.eyebrow')}</span>
            <h2>{t('home.ai.title')}</h2>
            <p className="section-copy">
              {t('home.ai.text')}
            </p>
          </div>
          <div className="feature-grid feature-grid-steps">
            {aiPillars.map((item) => (
              <article className="card feature-card feature-card-ai" key={item.title}>
                <h3>{item.title}</h3>
                <p>{item.text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.features.eyebrow')}</span>
            <h2>{t('home.features.title')}</h2>
          </div>
          <div className="feature-grid">
            {features.map((feature) => (
              <article className="card feature-card" key={feature.title}>
                <h3>{feature.title}</h3>
                <p>{feature.text}</p>
              </article>
            ))}
          </div>
          <p className="section-copy muted" style={{ marginTop: '1rem' }}>
            {t(platformNoteKey)}
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container trust-grid">
          <div>
            <div className="section-heading">
              <span className="eyebrow">{t('home.trust.eyebrow')}</span>
              <h2>{t('home.trust.title')}</h2>
            </div>
            <p className="section-copy">
              {t('home.trust.text')}
            </p>
          </div>
          <div className="card">
            <ul className="check-list">
              {trustItems.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <section className="section section-muted">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.faq.eyebrow')}</span>
            <h2>{t('home.faq.title')}</h2>
          </div>
          <div className="faq-list">
            {faqItems.map((item) => (
              <article className="card faq-card" key={item.question}>
                <h3>{item.question}</h3>
                <p>{item.answer}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {((!newsLoading && newsItems.length > 0) || newsSourceUrl) && (
        <section className="section">
          <div className="container">
            <div className="section-heading">
              <span className="eyebrow">{t('news.eyebrow')}</span>
              <h2>{t('news.title')}</h2>
            </div>
            {newsLoading && <p className="muted">{t('news.loading')}</p>}
            {!newsLoading && newsItems.length > 0 && (
              <div className="feature-grid">
                {newsItems.map((item, index) => {
                  const detailUrl = item.slug ? `/news/${item.slug}` : item.link
                  return (
                    <article
                      className={`card feature-card news-card news-card-compact${item.media_url ? ' has-media' : ''}`}
                      key={newsKey(item, index)}
                    >
                      {item.media_url && (
                        <img className="news-card-media" src={item.media_url} alt="" loading="lazy" />
                      )}
                      <div className="news-card-body">
                        <p className="muted news-card-date">{formatNewsDate(item.published_at, locale, t)}</p>
                        <h3>{item.title}</h3>
                        <p className="news-card-preview">{newsPreview(item.summary)}</p>
                        {detailUrl && (
                          <p className="news-card-link">
                            <a href={detailUrl}>{t('news.readMore')}</a>
                          </p>
                        )}
                      </div>
                    </article>
                  )
                })}
              </div>
            )}
            {!newsLoading && newsItems.length === 0 && (
              <p className="muted">{t('news.empty.default')}</p>
            )}
            {newsItems.length > 0 && (
              <p style={{ marginTop: '1rem' }}>
                <Link to="/news" className="btn btn-secondary btn-small">
                  {t('newsDetail.allNews')}
                </Link>
              </p>
            )}
          </div>
        </section>
      )}

      <section className="section cta-section">
        <div className="container cta-card">
          <div>
            <span className="eyebrow">{t('home.cta.eyebrow')}</span>
            <h2>{t('home.cta.title')}</h2>
          </div>
          <div className="hero-actions">
            <Link to="/pricing" className="btn">
              {t('common.pricing')}
            </Link>
            <Link to="/login" className="btn btn-secondary">
              {t('home.cta.try')}
            </Link>
          </div>
        </div>
      </section>
    </PublicLayout>
  )
}
