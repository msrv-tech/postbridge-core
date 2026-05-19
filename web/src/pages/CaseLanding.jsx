import { useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { getCaseLanding, translateCaseLanding } from '../caseLandings'
import { useI18n } from '../i18n'
import { reachMetrikaGoal } from '../metrika'
import { useAuth } from '../useAuth'

function useCaseCtaPath(slug) {
  const { user } = useAuth()
  const workspaceId = user?.workspaces?.[0]?.id || ''

  if (slug === 'ai-telegram-posts') {
    if (workspaceId) {
      return `/workspaces/${workspaceId}/content/new?case=${encodeURIComponent(slug)}`
    }
    return `/login?case=${encodeURIComponent(slug)}`
  }

  if (workspaceId) {
    return `/workspaces/${workspaceId}/migrate?case=${encodeURIComponent(slug)}`
  }
  return `/login?case=${encodeURIComponent(slug)}`
}

function CaseFlowPreview() {
  const { t } = useI18n()

  return (
    <div className="case-flow-preview" aria-label={t('case.flow.aria')}>
      <div className="case-flow-channel case-flow-channel-telegram">
        <span>{t('case.flow.source')}</span>
        <strong>Telegram</strong>
        <p>{t('case.flow.newPost')}</p>
      </div>
      <div className="case-flow-pipeline">
        <span className="case-flow-pulse" aria-hidden />
        <strong>Postbridge</strong>
        <p>{t('case.flow.pipeline')}</p>
      </div>
      <div className="case-flow-channel case-flow-channel-max">
        <span>{t('case.flow.destination')}</span>
        <strong>MAX</strong>
        <p>{t('case.flow.publication')}</p>
      </div>
      <div className="case-status-strip">
        <span className="case-status case-status-done">{t('case.status.published')}</span>
        <span className="case-status case-status-warning">{t('case.status.needsAttention')}</span>
        <span className="case-status case-status-error">{t('case.status.failed')}</span>
      </div>
    </div>
  )
}

const AI_POST_TONES = ['expert', 'easy', 'sales', 'news']

function aiPostKey(prefix, key) {
  return `case.aiTelegramPosts.${prefix}.${key}`
}

function AiTelegramDemo({ ctaPath, trackCta }) {
  const { t } = useI18n()
  const [topic, setTopic] = useState(t('case.aiTelegramPosts.demo.defaultTopic'))
  const [tone, setTone] = useState('expert')
  const [resultSeed, setResultSeed] = useState(0)

  const result = useMemo(() => {
    const cleanedTopic = topic.trim() || t('case.aiTelegramPosts.demo.defaultTopic')
    return {
      title: t(aiPostKey('demo', `${tone}.title`), { topic: cleanedTopic }),
      intro: t(aiPostKey('demo', `${tone}.intro`), { topic: cleanedTopic }),
      body: t(aiPostKey('demo', `${tone}.body`), { topic: cleanedTopic }),
      benefits: [1, 2, 3].map((number) => t(aiPostKey('demo', `${tone}.benefit${number}`))),
      cta: t(aiPostKey('demo', `${tone}.cta`)),
      tags: t(aiPostKey('demo', `${tone}.tags`)),
    }
  }, [topic, tone, resultSeed, t])

  return (
    <div className="ai-post-demo" aria-label={t('case.aiTelegramPosts.demo.aria')}>
      <div className="ai-post-demo-panel">
        <label className="field-label" htmlFor="ai-post-topic">
          {t('case.aiTelegramPosts.demo.topicLabel')}
        </label>
        <input
          id="ai-post-topic"
          className="input"
          type="text"
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
        />
        <div className="field-label">{t('case.aiTelegramPosts.demo.toneLabel')}</div>
        <div className="ai-post-tone-grid" role="radiogroup" aria-label={t('case.aiTelegramPosts.demo.toneLabel')}>
          {AI_POST_TONES.map((toneId) => (
            <button
              key={toneId}
              type="button"
              className={tone === toneId ? 'ai-post-tone is-active' : 'ai-post-tone'}
              aria-pressed={tone === toneId}
              onClick={() => setTone(toneId)}
            >
              {t(aiPostKey('tone', toneId))}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="btn ai-post-generate"
          onClick={() => {
            setResultSeed((value) => value + 1)
            trackCta('demo_generate')
          }}
        >
          {t('case.aiTelegramPosts.demo.generate')}
        </button>
        <Link to={ctaPath} className="btn btn-secondary" onClick={() => trackCta('demo_create')}>
          {t('case.aiTelegramPosts.primaryCta')}
        </Link>
      </div>
      <article className="ai-post-output" aria-live="polite">
        <span>{t('case.aiTelegramPosts.demo.outputLabel')}</span>
        <h3>{result.title}</h3>
        <p>{result.intro}</p>
        <p>{result.body}</p>
        <ul>
          {result.benefits.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p className="ai-post-output-cta">{result.cta}</p>
        <p className="ai-post-tags">{result.tags}</p>
      </article>
    </div>
  )
}

function AiTelegramPostsLanding({ ctaPath, trackCta }) {
  const { t } = useI18n()
  const [activeExample, setActiveExample] = useState(0)
  const examples = [1, 2, 3].map((number) => ({
    title: t(aiPostKey(`example${number}`, 'title')),
    idea: t(aiPostKey(`example${number}`, 'idea')),
    postTitle: t(aiPostKey(`example${number}`, 'postTitle')),
    paragraph1: t(aiPostKey(`example${number}`, 'paragraph1')),
    paragraph2: t(aiPostKey(`example${number}`, 'paragraph2')),
    benefits: [1, 2, 3].map((benefit) => t(aiPostKey(`example${number}`, `benefit${benefit}`))),
    cta: t(aiPostKey(`example${number}`, 'cta')),
    tags: t(aiPostKey(`example${number}`, 'tags')),
  }))
  const selectedExample = examples[activeExample] || examples[0]

  return (
    <PublicLayout>
      <section className="section case-hero-section">
        <div className="container ai-post-hero">
          <div className="case-hero-copy">
            <span className="eyebrow">{t('case.aiTelegramPosts.eyebrow')}</span>
            <h1 className="hero-title">{t('case.aiTelegramPosts.title')}</h1>
            <p className="hero-text">{t('case.aiTelegramPosts.subtitle')}</p>
            <div className="hero-actions">
              <Link to={ctaPath} className="btn" onClick={() => trackCta('create_post')}>
                {t('case.aiTelegramPosts.primaryCta')}
              </Link>
              <a href="#ai-post-examples" className="btn btn-secondary" onClick={() => trackCta('view_example')}>
                {t('case.aiTelegramPosts.secondaryCta')}
              </a>
            </div>
          </div>
          <AiTelegramDemo ctaPath={ctaPath} trackCta={trackCta} />
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('case.aiTelegramPosts.tasks.eyebrow')}</span>
            <h2>{t('case.aiTelegramPosts.tasks.title')}</h2>
          </div>
          <div className="ai-post-card-grid">
            {[1, 2, 3, 4, 5, 6].map((number) => (
              <article className="ai-post-compact-card" key={number}>
                {t(aiPostKey('tasks', String(number)))}
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section section-muted">
        <div className="container case-two-column">
          <div className="section-heading">
            <span className="eyebrow">{t('case.aiTelegramPosts.notChatgpt.eyebrow')}</span>
            <h2>{t('case.aiTelegramPosts.notChatgpt.title')}</h2>
            <p className="section-copy">{t('case.aiTelegramPosts.notChatgpt.text')}</p>
          </div>
          <div className="case-problem-solution">
            {[1, 2, 3, 4, 5].map((number) => (
              <div className="case-qualifier" key={number}>
                {t(aiPostKey('notChatgpt', String(number)))}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section" id="ai-post-examples">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('case.aiTelegramPosts.examples.eyebrow')}</span>
            <h2>{t('case.aiTelegramPosts.examples.title')}</h2>
          </div>
          <div className="ai-post-example-layout">
            <div className="case-scenario-tabs" role="tablist" aria-label={t('case.aiTelegramPosts.examples.aria')}>
              {examples.map((example, index) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeExample === index}
                  className={activeExample === index ? 'case-scenario-tab is-active' : 'case-scenario-tab'}
                  key={example.title}
                  onClick={() => setActiveExample(index)}
                >
                  {example.title}
                </button>
              ))}
            </div>
            <div className="ai-post-before-after">
              <article className="ai-post-before">
                <span>{t('case.aiTelegramPosts.examples.before')}</span>
                <p>{selectedExample.idea}</p>
              </article>
              <article className="ai-post-output">
                <span>{t('case.aiTelegramPosts.examples.after')}</span>
                <h3>{selectedExample.postTitle}</h3>
                <p>{selectedExample.paragraph1}</p>
                <p>{selectedExample.paragraph2}</p>
                <ul>
                  {selectedExample.benefits.map((benefit) => (
                    <li key={benefit}>{benefit}</li>
                  ))}
                </ul>
                <p className="ai-post-output-cta">{selectedExample.cta}</p>
                <p className="ai-post-tags">{selectedExample.tags}</p>
              </article>
            </div>
          </div>
        </div>
      </section>

      <section className="section section-muted">
        <div className="container ai-post-process">
          <div className="section-heading">
            <span className="eyebrow">{t('case.aiTelegramPosts.process.eyebrow')}</span>
            <h2>{t('case.aiTelegramPosts.process.title')}</h2>
          </div>
          <div className="feature-grid-steps">
            {[1, 2, 3].map((number) => (
              <article className="card" key={number}>
                <span className="step-number">{number}</span>
                <h3>{t(aiPostKey('process', `${number}.title`))}</h3>
                <p>{t(aiPostKey('process', `${number}.text`))}</p>
              </article>
            ))}
          </div>
          <Link to={ctaPath} className="btn" onClick={() => trackCta('process_cta')}>
            {t('case.aiTelegramPosts.process.cta')}
          </Link>
        </div>
      </section>

      <section className="section">
        <div className="container case-two-column">
          <div>
            <div className="section-heading">
              <span className="eyebrow">{t('case.aiTelegramPosts.audience.eyebrow')}</span>
              <h2>{t('case.aiTelegramPosts.audience.title')}</h2>
            </div>
            <div className="case-qualifier-grid">
              {[1, 2, 3, 4, 5].map((number) => (
                <div className="case-qualifier" key={number}>
                  {t(aiPostKey('audience', String(number)))}
                </div>
              ))}
            </div>
          </div>
          <article className="card case-proof-card">
            <span className="eyebrow">{t('case.aiTelegramPosts.cost.eyebrow')}</span>
            <h2>{t('case.aiTelegramPosts.cost.title')}</h2>
            <p>{t('case.aiTelegramPosts.cost.text')}</p>
            <p>{t('case.aiTelegramPosts.cost.economics')}</p>
          </article>
        </div>
      </section>

      <section className="section">
        <div className="container cta-card">
          <div>
            <span className="eyebrow">{t('case.aiTelegramPosts.final.eyebrow')}</span>
            <h2>{t('case.aiTelegramPosts.final.title')}</h2>
            <p className="section-copy">{t('case.aiTelegramPosts.final.text')}</p>
          </div>
          <Link to={ctaPath} className="btn" onClick={() => trackCta('bottom_create')}>
            {t('case.aiTelegramPosts.final.cta')}
          </Link>
        </div>
      </section>
    </PublicLayout>
  )
}

export default function CaseLanding() {
  const { t } = useI18n()
  const { slug = '' } = useParams()
  const landingConfig = getCaseLanding(slug)
  const landing = useMemo(() => translateCaseLanding(landingConfig, t), [landingConfig, t])
  const ctaPath = useCaseCtaPath(slug)
  const [activeScenario, setActiveScenario] = useState(0)

  const selectedScenario = useMemo(() => {
    if (!landing) return null
    if (landing.kind === 'aiTelegramPosts') return null
    return landing.scenarios[activeScenario] || landing.scenarios[0]
  }, [landing, activeScenario])

  useEffect(() => {
    if (!landing) return
    const title = landing.kind === 'aiTelegramPosts' ? t('case.aiTelegramPosts.title') : landing.title
    document.title = `${title} | Postbridge`
    reachMetrikaGoal('case_view', { case: landing.metrikaCase })
  }, [landing, t])

  if (!landing) return <Navigate to="/" replace />

  const trackCta = (action) => {
    reachMetrikaGoal('case_cta_clicked', {
      case: landing.metrikaCase,
      action,
    })
  }

  if (landing.kind === 'aiTelegramPosts') {
    return <AiTelegramPostsLanding ctaPath={ctaPath} trackCta={trackCta} />
  }

  return (
    <PublicLayout>
      <section className="section case-hero-section">
        <div className="container case-hero">
          <div className="case-hero-copy">
            <span className="eyebrow">{landing.eyebrow}</span>
            <h1 className="hero-title case-title">{landing.title}</h1>
            <p className="hero-text">{landing.subtitle}</p>
            <div className="hero-actions">
              <Link to={ctaPath} className="btn" onClick={() => trackCta('setup')}>
                {landing.primaryCta}
              </Link>
              <a
                href="#case-scenarios"
                className="btn btn-secondary"
                onClick={() => trackCta('scenarios')}
              >
                {landing.secondaryCta}
              </a>
            </div>
          </div>
          <CaseFlowPreview />
        </div>
      </section>

      <section className="section">
        <div className="container case-two-column">
          <div>
            <div className="section-heading">
              <span className="eyebrow">{t('case.sections.who.eyebrow')}</span>
              <h2>{t('case.sections.who.title')}</h2>
            </div>
            <div className="case-qualifier-grid">
              {landing.qualifiedFor.map((item) => (
                <div className="case-qualifier" key={item}>
                  {item}
                </div>
              ))}
            </div>
          </div>
          <div className="case-problem-solution">
            <article className="card">
              <h3>{t('case.sections.problem')}</h3>
              <ul className="check-list">
                {landing.problem.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </article>
            <article className="card">
              <h3>{t('case.sections.solution')}</h3>
              <ul className="check-list">
                {landing.solution.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </article>
          </div>
        </div>
      </section>

      <section className="section section-muted" id="case-scenarios">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('case.sections.scenarios.eyebrow')}</span>
            <h2>{t('case.sections.scenarios.title')}</h2>
          </div>
          <div className="case-scenario-layout">
            <div className="case-scenario-tabs" role="tablist" aria-label={t('case.sections.scenarios.aria')}>
              {landing.scenarios.map((scenario, index) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeScenario === index}
                  className={activeScenario === index ? 'case-scenario-tab is-active' : 'case-scenario-tab'}
                  key={scenario.title}
                  onClick={() => {
                    setActiveScenario(index)
                    reachMetrikaGoal('case_scenario_clicked', {
                      case: landing.metrikaCase,
                      scenario: scenario.title,
                    })
                  }}
                >
                  {scenario.title}
                </button>
              ))}
            </div>
            <article className="case-scenario-detail">
              <span>{t('case.sections.scenarios.number', { number: activeScenario + 1 })}</span>
              <h3>{selectedScenario.title}</h3>
              <p>{selectedScenario.text}</p>
              <Link to={ctaPath} className="btn btn-secondary" onClick={() => trackCta('scenario_next_step')}>
                {selectedScenario.nextStep}
              </Link>
            </article>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container case-two-column">
          <article className="card case-proof-card">
            <span className="eyebrow">{t('case.sections.proof.eyebrow')}</span>
            <h2>{t('case.sections.proof.title')}</h2>
            <ul className="check-list">
              {landing.supported.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </article>
          <article className="card case-not-promised-card">
            <span className="eyebrow">{t('case.sections.notPromised.eyebrow')}</span>
            <h2>{t('case.sections.notPromised.title')}</h2>
            <ul className="case-no-list">
              {landing.notPromised.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </article>
        </div>
      </section>

      <section className="section section-muted">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">FAQ</span>
            <h2>{t('case.sections.faq.title')}</h2>
          </div>
          <div className="faq-list">
            {landing.faq.map((item) => (
              <article className="card faq-card" key={item.question}>
                <h3>{item.question}</h3>
                <p>{item.answer}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container cta-card">
          <div>
            <span className="eyebrow">{t('case.sections.cta.eyebrow')}</span>
            <h2>{t('case.sections.cta.title')}</h2>
            <p className="section-copy">
              {t('case.sections.cta.text')}
            </p>
          </div>
          <div className="hero-actions">
            <Link to={ctaPath} className="btn" onClick={() => trackCta('bottom_setup')}>
              {landing.primaryCta}
            </Link>
            <Link to="/pricing" className="btn btn-secondary" onClick={() => trackCta('pricing')}>
              {t('common.pricing')}
            </Link>
          </div>
        </div>
      </section>
    </PublicLayout>
  )
}
