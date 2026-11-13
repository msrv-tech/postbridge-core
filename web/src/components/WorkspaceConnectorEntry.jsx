import { Link } from 'react-router-dom'
import { isSelfhostMode } from '../adapters/runtime'
import { useI18n } from '../i18n'
import './Connectors.css'

export default function WorkspaceConnectorEntry({ workspaceId }) {
  const { t } = useI18n()
  if (isSelfhostMode()) return null
  return <section className="workspace-connectors-entry" aria-labelledby="workspace-connectors-title">
    <div><h2 id="workspace-connectors-title">{t('connectors.title')}</h2><p>{t('connectors.workspace.text')}</p></div>
    <Link className="connector-catalog-link" to={`/workspaces/${encodeURIComponent(workspaceId)}/connectors`}>{t('connectors.open')} →</Link>
  </section>
}
