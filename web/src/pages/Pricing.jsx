import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import PublicLayout from '../components/PublicLayout'
import { useAuth } from '../useAuth'
import { reachMetrikaGoal } from '../metrika'
import { useI18n } from '../i18n'

function getPlans(t) {
  return [
  {
    code: 'free',
    name: 'Free',
    price: '0 ₽',
    priceStars: null,
    period: t('pricing.period.forever'),
    description: t('pricing.free.description'),
    features: [
      t('pricing.free.feature.bridges'),
      t('pricing.free.feature.aiDrafts'),
      t('pricing.free.feature.noAiAdaptation'),
      t('pricing.free.feature.postLimit'),
      t('pricing.free.feature.import'),
    ],
  },
  {
    code: 'pro',
    name: 'Pro',
    price: '1 990 ₽',
    priceStars: 1109,
    period: t('pricing.period.month'),
    description: t('pricing.pro.description'),
    featured: true,
    features: [
      t('pricing.pro.feature.bridges'),
      t('pricing.pro.feature.aiBudget'),
      t('pricing.pro.feature.aiAdaptation'),
      t('pricing.pro.feature.unlimitedPosts'),
      t('pricing.feature.importIncluded'),
    ],
  },
  {
    code: 'agency',
    name: 'Agency',
    price: '5 990 ₽',
    priceStars: 3330,
    period: t('pricing.period.month'),
    description: t('pricing.agency.description'),
    features: [
      t('pricing.agency.feature.bridges'),
      t('pricing.agency.feature.aiBudget'),
      t('pricing.agency.feature.aiAdaptation'),
      t('pricing.pro.feature.unlimitedPosts'),
      t('pricing.feature.importIncluded'),
    ],
  },
]
}

function getOneTimeProducts(t) {
  return [
  {
    name: t('pricing.oneTime.import.name'),
    description: t('pricing.oneTime.import.description'),
    priceRub: 500,
    priceStars: 280,
  },
]
}

function getComparisonRows(t) {
  return [
    [t('pricing.compare.bridges'), '1', '3', '10'],
    [t('pricing.compare.posts'), t('pricing.compare.upTo20'), t('pricing.compare.unlimited'), t('pricing.compare.unlimited')],
    [t('pricing.compare.aiContent'), t('pricing.compare.minimal'), t('pricing.compare.needed'), t('pricing.compare.triplePro')],
    [t('pricing.compare.aiAdaptation'), t('common.no'), t('common.yes'), t('common.yes')],
    [t('pricing.compare.import'), t('pricing.compare.importFreeOneTime'), t('pricing.compare.included'), t('pricing.compare.included')],
    [t('pricing.compare.analytics'), t('common.no'), t('pricing.compare.basic'), t('common.yes')],
  ]
}

export default function Pricing() {
  const { t } = useI18n()
  const { user } = useAuth()
  const workspaceId = user?.workspaces?.[0]?.id || ''
  const plans = getPlans(t)
  const oneTimeProducts = getOneTimeProducts(t)
  const comparisonRows = getComparisonRows(t)
  useEffect(() => {
    reachMetrikaGoal('pricing_view')
  }, [])
  const planActionUrl = (plan) =>
    workspaceId
      ? `/workspaces/${workspaceId}/settings?billing=change-plan&plan=${encodeURIComponent(plan.code)}`
      : `/login?plan=${encodeURIComponent(plan.code)}`

  return (
    <PublicLayout>
      <section className="section">
        <div className="container">
          <div className="section-heading section-heading-center">
            <span className="eyebrow">{t('common.pricing')}</span>
            <h1 className="hero-title hero-title-small">{t('pricing.hero.title')}</h1>
            <p className="hero-text hero-text-centered">
              {t('pricing.hero.text')}
            </p>
          </div>

          <div className="pricing-grid">
            {plans.map((plan) => (
              <article
                key={plan.name}
                className={plan.featured ? 'card pricing-card pricing-card-featured' : 'card pricing-card'}
              >
                <div className="pricing-head">
                  <div>
                    <h2>{plan.name}</h2>
                    <p>{plan.description}</p>
                  </div>
                  {plan.featured && <span className="badge badge-running">{t('pricing.recommended')}</span>}
                </div>
                <div className="pricing-price">
                  <strong className="pricing-price-line">{plan.price}</strong>
                  {plan.priceStars != null && <span className="pricing-stars">{t('pricing.starsAlternative', { stars: plan.priceStars })}</span>}
                  <span>{plan.period}</span>
                </div>
                <ul className="check-list">
                  {plan.features.map((feature) => (
                    <li key={feature}>{feature}</li>
                  ))}
                </ul>
                <Link
                  to={planActionUrl(plan)}
                  className={plan.featured ? 'btn' : 'btn btn-secondary'}
                  onClick={() =>
                    reachMetrikaGoal('plan_selected', {
                      plan: plan.code,
                      source: 'pricing',
                      paid: plan.priceStars != null,
                    })
                  }
                >
                  {plan.name === 'Free' ? t('pricing.actions.startFree') : t('pricing.actions.choosePlan')}
                </Link>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section section-muted">
        <div className="container">
          <div className="section-heading section-heading-center">
            <span className="eyebrow">{t('pricing.oneTime.eyebrow')}</span>
            <h2>{t('pricing.oneTime.title')}</h2>
            <p className="hero-text hero-text-centered">
              {t('pricing.oneTime.text')}
            </p>
          </div>
          <div className="one-time-grid">
            {oneTimeProducts.map((product) => (
              <article key={product.name} className="card one-time-card">
                <div className="one-time-card-body">
                  <div>
                    <h2 className="one-time-title">{product.name}</h2>
                    <p className="one-time-desc">{product.description}</p>
                  </div>
                  <div className="one-time-price">
                    <strong>{product.priceRub} ₽</strong>
                    <span className="muted">/ {product.priceStars} ⭐</span>
                    <span className="one-time-period">{t('pricing.oneTime.import.period')}</span>
                  </div>
                </div>
                <Link
                  to="/login"
                  className="btn btn-secondary"
                  onClick={() => reachMetrikaGoal('one_time_product_selected', { product: product.name })}
                >
                  {t('pricing.oneTime.action')}
                </Link>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="section-heading">
            <span className="eyebrow">{t('pricing.compare.eyebrow')}</span>
            <h2>{t('pricing.compare.title')}</h2>
            <p className="section-copy">
              {t('pricing.compare.text')}
            </p>
          </div>

          <div className="table-card">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>{t('pricing.compare.capability')}</th>
                  <th>Free</th>
                  <th>Pro</th>
                  <th>Agency</th>
                </tr>
              </thead>
              <tbody>
                {comparisonRows.map(([label, free, pro, agency]) => (
                  <tr key={label}>
                    <td>{label}</td>
                    <td>{free}</td>
                    <td>{pro}</td>
                    <td>{agency}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container cta-card">
          <div>
            <span className="eyebrow">{t('pricing.important.eyebrow')}</span>
            <h2>
              {t('pricing.important.title')}
            </h2>
            <p className="section-copy">
              {t('pricing.important.text')}
            </p>
          </div>
          <div className="hero-actions">
            <Link
              to="/login"
              className="btn"
              onClick={() => reachMetrikaGoal('launch_clicked', { source: 'pricing_bottom' })}
            >
              {t('pricing.important.action')}
            </Link>
          </div>
        </div>
      </section>
    </PublicLayout>
  )
}
