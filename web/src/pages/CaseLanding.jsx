import { useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { getCaseLanding } from '../caseLandings'
import { reachMetrikaGoal } from '../metrika'
import { useAuth } from '../useAuth'

function useCaseCtaPath(slug) {
  const { user } = useAuth()
  const workspaceId = user?.workspaces?.[0]?.id || ''

  if (workspaceId) {
    return `/workspaces/${workspaceId}/migrate?case=${encodeURIComponent(slug)}`
  }
  return `/login?case=${encodeURIComponent(slug)}`
}

function CaseFlowPreview() {
  return (
    <div className="case-flow-preview" aria-label="Telegram to MAX publication flow">
      <div className="case-flow-channel case-flow-channel-telegram">
        <span>Источник</span>
        <strong>Telegram</strong>
        <p>Новый пост в канале</p>
      </div>
      <div className="case-flow-pipeline">
        <span className="case-flow-pulse" aria-hidden />
        <strong>Postbridge</strong>
        <p>Проверка, очередь, доставка</p>
      </div>
      <div className="case-flow-channel case-flow-channel-max">
        <span>Направление</span>
        <strong>MAX</strong>
        <p>Публикация в канале</p>
      </div>
      <div className="case-status-strip">
        <span className="case-status case-status-done">Опубликовано</span>
        <span className="case-status case-status-warning">Требует внимания</span>
        <span className="case-status case-status-error">Ошибка</span>
      </div>
    </div>
  )
}

export default function CaseLanding() {
  const { slug = '' } = useParams()
  const landing = getCaseLanding(slug)
  const ctaPath = useCaseCtaPath(slug)
  const [activeScenario, setActiveScenario] = useState(0)

  const selectedScenario = useMemo(() => {
    if (!landing) return null
    return landing.scenarios[activeScenario] || landing.scenarios[0]
  }, [landing, activeScenario])

  useEffect(() => {
    if (!landing) return
    document.title = `${landing.title} | Postbridge`
    reachMetrikaGoal('case_view', { case: landing.metrikaCase })
  }, [landing])

  if (!landing) return <Navigate to="/" replace />

  const trackCta = (action) => {
    reachMetrikaGoal('case_cta_clicked', {
      case: landing.metrikaCase,
      action,
    })
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
              <span className="eyebrow">Для кого</span>
              <h2>Для каналов, команд и регулярных публикаций</h2>
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
              <h3>Проблема</h3>
              <ul className="check-list">
                {landing.problem.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </article>
            <article className="card">
              <h3>Решение</h3>
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
            <span className="eyebrow">Сценарии</span>
            <h2>Выберите, как команда будет переносить контент</h2>
          </div>
          <div className="case-scenario-layout">
            <div className="case-scenario-tabs" role="tablist" aria-label="Сценарии переноса">
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
              <span>Сценарий {activeScenario + 1}</span>
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
            <span className="eyebrow">Что видно в работе</span>
            <h2>Связка Telegram → MAX с понятными статусами</h2>
            <ul className="check-list">
              {landing.supported.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </article>
          <article className="card case-not-promised-card">
            <span className="eyebrow">Что не обещаем</span>
            <h2>Не смешиваем кросспостинг с бытовой миграцией</h2>
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
            <h2>Частые вопросы перед настройкой</h2>
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
            <span className="eyebrow">Готово к настройке</span>
            <h2>Запустите первый поток Telegram → MAX</h2>
            <p className="section-copy">
              Начните с одной связки, проверьте доставку и расширяйте процесс после первых публикаций.
            </p>
          </div>
          <div className="hero-actions">
            <Link to={ctaPath} className="btn" onClick={() => trackCta('bottom_setup')}>
              {landing.primaryCta}
            </Link>
            <Link to="/pricing" className="btn btn-secondary" onClick={() => trackCta('pricing')}>
              Тарифы
            </Link>
          </div>
        </div>
      </section>
    </PublicLayout>
  )
}
