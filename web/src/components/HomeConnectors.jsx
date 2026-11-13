import { useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useI18n } from '../i18n'
import useConnectors from '../useConnectors'
import { ConnectorGroups } from './ConnectorCatalog'

export default function HomeConnectors() {
  const { t } = useI18n()
  const { hash } = useLocation()
  const section = useRef(null)
  const { items, loading, error, disabled } = useConnectors()
  const featured = items.filter(item => item.featured === true && item.public_readiness !== 'planned')
  const hasDisabled = items.some(item => item.rollout === 'disabled')
  useEffect(() => {
    if (hash !== '#connectors' || loading) return undefined
    const frame = requestAnimationFrame(() => section.current?.scrollIntoView({ block: 'start' }))
    return () => cancelAnimationFrame(frame)
  }, [hash, loading])
  if (disabled || (!loading && !error && !featured.length)) return null
  return <section className="section home-connectors" id="connectors" ref={section} aria-labelledby="home-connectors-title">
    <div className="container">
      <div className="connector-section-heading">
        <div><span className="eyebrow">{t('connectors.title')}</span><h2 id="home-connectors-title">{t('connectors.home.title')}</h2>
          <p className="section-copy">{t('connectors.home.text')}</p></div>
        <Link className="connector-catalog-link" to="/connectors">{t('connectors.browse')} <span aria-hidden="true">→</span></Link>
      </div>
      {loading ? <p role="status" className="muted">{t('common.loading')}</p> : error ? <p className="muted">{t('connectors.unavailable')}</p> : <>
        <div className="connector-content-flow" aria-label={t('connectors.flow.label')}>
          <span>{t('connectors.flow.data')}</span><span aria-hidden="true">→</span><strong>{t('connectors.flow.content')}</strong>
          <span aria-hidden="true">→</span><a href="/home#platforms">{t('connectors.flow.channels')}</a>
        </div>
        <ConnectorGroups items={featured} headingLevel={3} />
        {items.some(item => item.public_readiness === 'planned') && <div className="connector-roadmap-link">
          <span>{t(hasDisabled ? 'connectors.home.unavailable' : 'connectors.home.roadmap')}</span><Link to="/connectors#planned">{t(hasDisabled ? 'connectors.other' : 'connectors.plans')} →</Link>
        </div>}
      </>}
    </div>
  </section>
}
