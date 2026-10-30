import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { listNews } from '../adapters/news'
import { useI18n } from '../i18n'
import { getPublicMarket } from '../branding'

const demoBase = `${import.meta.env.BASE_URL}demo/`

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
    title: t('home.steps.create.title'),
    text: t('home.steps.create.text'),
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

function getSupportedPlatforms(t, region) {
  if (region === 'io') {
    return [
      {
        key: 'telegram',
        name: t('platform.telegram'),
      },
      {
        key: 'linkedin',
        name: t('platform.linkedin'),
      },
      {
        key: 'x',
        name: t('platform.x'),
      },
      {
        key: 'bluesky',
        name: t('platform.bluesky'),
      },
      {
        key: 'mastodon',
        name: t('platform.mastodon'),
      },
      {
        key: 'rss',
        name: t('platform.rss'),
      },
    ]
  }

  return [
    {
      key: 'telegram',
      name: t('platform.telegram'),
    },
    {
      key: 'vk',
      name: t('platform.vk'),
    },
    {
      key: 'max',
      name: t('platform.max'),
    },
    {
      key: 'rss',
      name: t('platform.rss'),
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
      { from: 'RSS', to: t('platform.bluesky'), fromKey: 'rss', toKey: 'bluesky', label: t('home.bridge.direct.rssToBluesky') },
      { from: 'RSS', to: t('platform.mastodon'), fromKey: 'rss', toKey: 'mastodon', label: t('home.bridge.direct.rssToMastodon') },
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
  ...['materials', 'channels', 'cost'].map((key) => ({
    question: t(`home.faq.${key}.question`),
    answer: t(`home.faq.${key}.answer`),
  })),
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
  const landingRegion = getPublicMarket()
  const demoVideo = `${demoBase}coffee-video-${locale === 'en' ? 'en' : 'ru'}`
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
  const supportedPlatforms = getSupportedPlatforms(t, landingRegion)
  const features = getFeatures(t, landingRegion)
  const trustItems = getTrustItems(t)
  const agentBridgeExamples = getAgentBridgeExamples(t, landingRegion)
  const platformBridgeExamples = getPlatformBridgeExamples(t, landingRegion)
  const faqItems = getFaqItems(t, landingRegion)
  const platformNoteKey = getRegionalKey(landingRegion, 'home.features.platformNote')
  const heroTextKey = getRegionalKey(landingRegion, 'home.hero.text')

  return (
    <PublicLayout className="public-home">
      <section className="hero hero-ai content-studio-hero">
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
                {t('home.hero.create')}
              </Link>
              <a href="#examples" className="btn btn-secondary">{t('home.hero.examples')}</a>
            </div>
            <p className="studio-hero-note">{t('home.hero.note')}</p>
          </div>

          <div className="studio-showcase" id="examples" aria-label={t('home.examples.title')}>
            <p className="studio-showcase-label">{t('home.examples.title')}</p>
            <div className="studio-showcase-grid">
              <div className="studio-showcase-side">
                <article className="card studio-post-example">
                  <span className="studio-format-label">{t('home.examples.post.label')}</span>
                  <h2>{t('home.examples.post.title')}</h2>
                  <p>{t('home.examples.post.text')}</p>
                </article>
                <figure className="studio-image-example">
                  <img src={`${demoBase}coffee-cover.jpg`} alt={t('home.examples.image.alt')} width="960" height="720" />
                  <figcaption>{t('home.examples.image.label')}</figcaption>
                </figure>
              </div>
              <figure className="studio-video-example">
                <video key={demoVideo} controls playsInline preload="none" poster={`${demoVideo}-poster.jpg`} width="540" height="960" aria-label={t('home.examples.video.label')}>
                  <source src={`${demoVideo}.mp4`} type="video/mp4" />
                  <a href={`${demoVideo}.mp4`}>{t('home.examples.video.download')}</a>
                </video>
                <figcaption>{t('home.examples.video.label')}<span>{t('home.examples.video.note')}</span></figcaption>
              </figure>
            </div>
          </div>
        </div>
      </section>

      <section className="section section-muted" id="formats">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.formats.eyebrow')}</span>
            <h2>{t('home.formats.title')}</h2>
          </div>
          <div className="feature-grid feature-grid-steps">
            {['text', 'image', 'video'].map((format, index) => (
              <article className="card feature-card studio-format-card" key={format}>
                <span className="step-number">0{index + 1}</span>
                <h3>{t(`home.formats.${format}.title`)}</h3>
                <p>{t(`home.formats.${format}.text`)}</p>
                <span className="studio-format-detail">{t(`home.formats.${format}.detail`)}</span>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.how.eyebrow')}</span>
            <h2>{t('home.how.title')}</h2>
          </div>
          <div className="feature-grid studio-process-grid">
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

      <section className="section section-ai" id="video-studio">
        <div className="container trust-grid studio-video-section">
          <div className="section-heading">
            <span className="eyebrow">{t('home.video.eyebrow')}</span>
            <h2>{t('home.video.title')}</h2>
            <p className="section-copy">{t('home.video.text')}</p>
            <Link to="/login" className="btn btn-secondary">{t('home.video.cta')}</Link>
          </div>
          <div className="card">
            <ul className="check-list">
              {['script', 'visuals', 'voice', 'versions', 'export'].map((key) => <li key={key}>{t(`home.video.${key}`)}</li>)}
            </ul>
          </div>
        </div>
      </section>

      <section className="section section-ai">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.ai.eyebrow')}</span>
            <h2>{t('home.ai.title')}</h2>
            <p className="section-copy">{t('home.ai.text')}</p>
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

      <section className="section section-platforms" id="platforms">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('home.platforms.eyebrow')}</span>
            <h2>{t(getRegionalKey(landingRegion, 'home.platforms.title'))}</h2>
          </div>
          <div className="platform-showcase" aria-label={t('home.platforms.aria')}>
            {supportedPlatforms.map((platform) => (
              <article className={`platform-showcase-item platform-showcase-item-${platform.key}`} key={platform.key}>
                <span className={`platform-glyph platform-glyph-${platform.key}`} aria-hidden>
                  {platform.name.slice(0, 2)}
                </span>
                <h3>{platform.name}</h3>
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
