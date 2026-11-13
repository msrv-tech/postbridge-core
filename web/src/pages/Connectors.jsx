import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import PublicLayout from '../components/PublicLayout'
import { ConnectorAgentLinks, ConnectorAvailability, ConnectorGroups, ConnectorLogo, ConnectorPersonalConnection, ConnectorProtocol, ConnectorReadiness, ConnectorRoadmap, connectorPath } from '../components/ConnectorCatalog'
import { getPublicExternalAppManifest } from '../adapters/externalApps'
import { connectorAllowsSetup, safeConnectorHref } from '../adapters/connectorPolicy'
import { preferredWorkspaceId } from '../adapters/workspace'
import { BILLING_SUPPORT_EMAIL } from '../billingSupport'
import { connectorCopy } from '../connectorPresentation'
import { useI18n } from '../i18n'
import { useAuth } from '../useAuth'
import useConnectors from '../useConnectors'

function ConnectorDetail({ item, workspaceId, user }) {
  const { t } = useI18n()
  const copy = connectorCopy(item, t)
  const canSetup = connectorAllowsSetup(item)
  const [workspaceChoice, setWorkspaceChoice] = useState(() => preferredWorkspaceId(user))
  const [manifestResult, setManifestResult] = useState(null)
  const workspaces = user?.workspaces || []
  const chosenWorkspace = workspaces.find(workspace => workspace.id === workspaceChoice)
  const activeWorkspace = workspaces.find(workspace => workspace.id === workspaceId)
  const listPath = workspaceId ? `/workspaces/${encodeURIComponent(workspaceId)}/connectors` : '/connectors'
  const mcp = item.kind === 'mcp'
  useEffect(() => {
    if (!workspaces.some(workspace => workspace.id === workspaceChoice)) setWorkspaceChoice(preferredWorkspaceId(user))
  }, [user, workspaceChoice])
  useEffect(() => {
    if (!canSetup) return undefined
    let active = true
    getPublicExternalAppManifest(item.id).then(manifest => {
      if (active) setManifestResult({ id: item.id, manifest })
    }).catch(() => { if (active) setManifestResult(null) })
    return () => { active = false }
  }, [item.id, canSetup])
  const manifest = manifestResult?.id === item.id ? manifestResult.manifest : null
  const docsUrl = safeConnectorHref(mcp ? manifest?.mcp?.docs_url || '/mcp/docs' : manifest?.documentation_url, { external: true })
  const providerDocs = mcp && safeConnectorHref(manifest?.documentation_url, { external: true })
  const serverUrl = safeConnectorHref(manifest?.mcp?.server_url, { external: true })
  const manifestUrl = safeConnectorHref(item.manifest_url)
  return <article className="connector-detail" data-connector-detail={item.id} data-readiness={item.public_readiness} data-rollout={item.rollout}>
    <Link className="connector-back" to={listPath}>← {t('connectors.all')}</Link>
    <div className="connector-card-top"><ConnectorLogo item={item} /><ConnectorReadiness item={item} /></div>
    {!workspaceId && <h1>{copy.name}</h1>}
    <ConnectorProtocol item={item} />
    <p className="connector-benefit">{copy.benefit}</p>
    <section className="connector-detail-section" aria-labelledby="connector-availability-title">
      <h2 id="connector-availability-title">{t('connectors.current')}</h2>
      <ConnectorAvailability item={item} />
      {item.public_readiness === 'planned' && item.rollout !== 'disabled' && <p>{t('connectors.planned.detail')}</p>}
      {workspaceId && item.public_readiness !== 'planned' && <ConnectorPersonalConnection item={item} />}
    </section>
    {(canSetup || item.rollout === 'closed_pilot') && <section className="connector-detail-section" aria-labelledby="connector-steps-title">
      <h2 id="connector-steps-title">{t(item.rollout === 'closed_pilot' ? 'connectors.pilot.title' : 'connectors.steps')}</h2>
      <ol>{copy.steps.map(step => <li key={step}>{step}</li>)}</ol>
      {canSetup && !user && <div className="connector-actions">
        <Link className="btn" to={`/login?connector=${encodeURIComponent(item.id)}`}>{t('connectors.signIn')}</Link>
      </div>}
      {canSetup && user && !workspaceId && <div className="connector-workspace-choice">
        {workspaces.length > 1 ? <div><label htmlFor="connector-workspace">{t('connectors.workspace.choose')}</label>
          <select id="connector-workspace" value={chosenWorkspace?.id || ''} onChange={event => setWorkspaceChoice(event.target.value)}>
            {workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
          </select>
        </div> : chosenWorkspace ? <strong>{chosenWorkspace.name}</strong> : null}
        <p>{t(mcp ? 'connectors.scopeExplanation.user_client' : 'connectors.scopeExplanation.workspace')}</p>
        {chosenWorkspace ? <div className="connector-actions"><Link className="btn" to={connectorPath(item, chosenWorkspace.id)}>{t('connectors.workspace.continue')}</Link></div>
          : <p>{t('connectors.workspace.none')}</p>}
      </div>}
      {canSetup && workspaceId && <>
        <div className="connector-workspace-choice"><strong>{activeWorkspace?.name || t('connectors.workspace.current')}</strong>
          <p>{t(mcp ? 'connectors.scopeExplanation.user_client' : 'connectors.scopeExplanation.workspace')}</p></div>
        <div className="connector-actions">
          {docsUrl && <a className="btn" href={docsUrl} target="_blank" rel="noopener noreferrer">{t('connectors.readGuide')}</a>}
          {providerDocs && providerDocs !== docsUrl && <a className="btn btn-secondary" href={providerDocs} target="_blank" rel="noopener noreferrer">{t('connectors.providerGuide', { name: copy.name })}</a>}
          <Link className="btn btn-secondary" to={`/workspaces/${encodeURIComponent(workspaceId)}/settings#ecosystem-apps`}>{t('connectors.workspace.settings')}</Link>
        </div>
      </>}
      {item.rollout === 'closed_pilot' && <div className="connector-actions"><a className="btn btn-secondary" href={`mailto:${BILLING_SUPPORT_EMAIL}`}>{t('connectors.pilot.contact')}</a></div>}
      <p className="connector-note">{t('connectors.consent')}</p>
    </section>}
    {canSetup && <details className="connector-agent-links"><summary>{t('connectors.configuration')}</summary>
      {serverUrl && <p>{t('connectors.serverUrl')}<a className="connector-config-url" href={serverUrl}>{new URL(serverUrl, window.location.origin).href}</a></p>}
      {manifestUrl && <p><a href={manifestUrl}>{t('connectors.manifest')}</a></p>}
      <p>{t('connectors.agents.consent')}</p>
    </details>}
    <ConnectorAgentLinks />
  </article>
}

export default function Connectors() {
  const { t } = useI18n()
  const { workspaceId = '', connectorId = '' } = useParams()
  const { user } = useAuth()
  const { items, loading, error, disabled } = useConnectors(workspaceId)
  const roadmapRef = useRef(null)
  const item = items.find(entry => entry.id === connectorId)
  const title = item ? connectorCopy(item, t).name : t('connectors.title')
  const Content = workspaceId ? 'main' : 'div'
  useEffect(() => {
    if (!loading && window.location.hash === '#planned') roadmapRef.current?.querySelector('#planned')?.scrollIntoView({ block: 'start' })
  }, [loading])
  const content = <Content className="connector-catalog" ref={roadmapRef}>
    {!workspaceId && (!connectorId || !item) && <header className="connector-page-heading"><h1>{t('connectors.title')}</h1><p>{t('connectors.catalog.text')}</p></header>}
    {loading ? <p role="status" className="muted">{t('common.loading')}</p>
      : disabled ? <p className="muted">{t('connectors.selfhost')}</p>
        : error ? <p role="alert">{t('connectors.unavailable')}</p>
          : connectorId ? item ? <ConnectorDetail key={`${workspaceId}/${item.id}`} item={item} workspaceId={workspaceId} user={user} />
            : <div><h2>{t('connectors.notFound')}</h2><p className="muted">{t('connectors.notFound.text')}</p><Link className="connector-back" to={workspaceId ? `/workspaces/${encodeURIComponent(workspaceId)}/connectors` : '/connectors'}>{t('connectors.all')}</Link></div>
            : <>
              {!items.length && <p className="muted">{t('connectors.empty')}</p>}
              <ConnectorGroups items={items.filter(entry => entry.public_readiness !== 'planned')} workspaceId={workspaceId} />
              <ConnectorRoadmap items={items.filter(entry => entry.public_readiness === 'planned')} workspaceId={workspaceId} />
              {items.length > 0 && <ConnectorAgentLinks />}
            </>}
  </Content>
  return workspaceId ? <AppShell title={title} subtitle={t('connectors.workspace.text')} user={user} workspaceId={workspaceId}>{content}</AppShell>
    : <PublicLayout><section className="section"><div className="container">{content}</div></section></PublicLayout>
}
