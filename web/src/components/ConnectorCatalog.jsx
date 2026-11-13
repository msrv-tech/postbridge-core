import { useId } from 'react'
import { Link } from 'react-router-dom'
import { connectorAllowsSetup, connectorPersonalState } from '../adapters/connectorPolicy'
import { connectorCopy, connectorLogo, connectorStatusLabel, mcpLogoUrl } from '../connectorPresentation'
import { useI18n } from '../i18n'
import './Connectors.css'

export function connectorPath(item, workspaceId = '') {
  const base = workspaceId ? `/workspaces/${encodeURIComponent(workspaceId)}/connectors` : '/connectors'
  return `${base}/${encodeURIComponent(item.id)}`
}

export function ConnectorLogo({ item }) {
  const logo = connectorLogo(item)
  return logo && <span className={`connector-logo connector-logo-${logo.shape}`} aria-hidden="true">
    <img src={logo.src} alt="" draggable="false" width="100" height="36" />
  </span>
}

export function ConnectorProtocol({ item }) {
  const { t } = useI18n()
  return item.brand_key === 'openai' && item.kind === 'mcp' && <p className="connector-protocol">
    <img src={mcpLogoUrl} alt="" width="20" height="20" />{t('connectors.protocol')}
  </p>
}

export function ConnectorReadiness({ item }) {
  const { t } = useI18n()
  return <span className={`connector-readiness is-${item.rollout === 'disabled' ? 'unavailable' : item.public_readiness}`}>
    <span className="connector-sr-only">{t(item.rollout === 'disabled' ? 'connectors.availabilityLabel' : 'connectors.readinessLabel')} </span>
    {connectorStatusLabel(item, t)}
  </span>
}

export function ConnectorPersonalConnection({ item }) {
  const { t } = useI18n()
  const personal = connectorPersonalState(item)
  return <dl className={`connector-personal is-${personal.state}`} data-connection-scope={personal.scope}>
    <dt>{t(`connectors.scope.${personal.scope}`)}</dt>
    <dd>{personal.accountName && <>{personal.accountName} · </>}{t(`connectors.personal.${personal.state}`)}</dd>
  </dl>
}

export function ConnectorAvailability({ item }) {
  const { t } = useI18n()
  const key = ['public', 'existing_installations', 'manual_setup', 'closed_pilot', 'not_launched', 'disabled'].includes(item.rollout) ? item.rollout : 'unknown'
  return <p className="connector-note">{t(item.rollout === 'closed_pilot' ? 'connectors.pilotNote' : `connectors.availability.${key}`)}</p>
}

function ConnectorCard({ item, workspaceId, headingLevel }) {
  const { t } = useI18n()
  const headingId = useId()
  const Heading = `h${headingLevel}`
  const copy = connectorCopy(item, t)
  const personal = connectorPersonalState(item)
  const canSetup = connectorAllowsSetup(item)
  const planned = item.public_readiness === 'planned' || ['not_launched', 'disabled'].includes(item.rollout)
  const action = planned ? 'details' : workspaceId && personal.state === 'needs_login' ? 'reauth'
    : workspaceId && personal.state === 'connected' ? 'manage' : item.rollout === 'closed_pilot' ? 'pilot'
      : canSetup && item.setup_mode === 'manual_mcp' ? 'mcp' : canSetup ? 'setup' : 'guide'
  return <li>
    <article className="connector-card" data-connector-id={item.id} data-connector-kind={item.kind}
      data-readiness={item.public_readiness} data-rollout={item.rollout} aria-labelledby={headingId}>
      <div className="connector-card-top"><ConnectorLogo item={item} /><ConnectorReadiness item={item} /></div>
      <Heading id={headingId}>{copy.name}</Heading>
      <ConnectorProtocol item={item} />
      {planned && <p className="connector-note">{t(`connectors.kind.${item.kind}`)}</p>}
      <p className="connector-benefit">{copy.benefit}</p>
      <ConnectorAvailability item={item} />
      {workspaceId && !planned && <ConnectorPersonalConnection item={item} />}
      <Link className="connector-cta" to={connectorPath(item, workspaceId)} aria-label={t(`connectors.action.${action}`, { name: copy.name })}>
        {t(`connectors.action.${action}`, { name: copy.name })}<span aria-hidden="true"> ↗</span>
      </Link>
    </article>
  </li>
}

export function ConnectorGroups({ items, workspaceId = '', headingLevel = 2 }) {
  const { t } = useI18n()
  const id = useId()
  const kinds = ['data', 'mcp'].filter(kind => items.some(item => item.kind === kind))
  const Heading = `h${headingLevel}`
  return <div className="connector-groups" data-single={kinds.length === 1}>
    {kinds.map(kind => <section key={kind} aria-labelledby={`${id}-${kind}`}>
      <Heading id={`${id}-${kind}`}>{t(`connectors.group.${kind}`)}</Heading>
      <p className="connector-group-copy">{t(`connectors.group.${kind}.text`)}</p>
      <ul className="connector-list">
        {items.filter(item => item.kind === kind).map(item => <ConnectorCard key={item.id} item={item} workspaceId={workspaceId} headingLevel={headingLevel + 1} />)}
      </ul>
    </section>)}
  </div>
}

export function ConnectorRoadmap({ items, workspaceId = '' }) {
  const { t } = useI18n()
  if (!items.length) return null
  const ordered = ['data', 'mcp'].flatMap(kind => items.filter(item => item.kind === kind))
  const hasDisabled = items.some(item => item.rollout === 'disabled')
  return <section className="connector-roadmap" id="planned" aria-labelledby="connector-planned-title">
    <h2 id="connector-planned-title">{t(hasDisabled ? 'connectors.unavailable.title' : 'connectors.planned.title')}</h2>
    <p className="connector-group-copy">{t('connectors.planned.text')}</p>
    <ul className="connector-list">
      {ordered.map(item => <ConnectorCard key={item.id} item={item} workspaceId={workspaceId} headingLevel={3} />)}
    </ul>
  </section>
}

export function ConnectorAgentLinks() {
  const { t } = useI18n()
  return <details className="connector-agent-links">
    <summary>{t('connectors.agents')}</summary>
    <ul><li><a href="/api/connectors">{t('connectors.agents.catalog')}</a></li>
      <li><a href="/mcp/docs">{t('connectors.agents.mcp')}</a></li>
      <li>{t('connectors.agents.consent')}</li></ul>
  </details>
}
