import { Link, useNavigate, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import AgentSectionLayout from '../components/AgentSectionLayout'
import { clearToken } from '../adapters/sessionToken'
import { useAuth } from '../useAuth'
import { useI18n } from '../i18n'

export default function AgentEditor() {
  const { t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  return (
    <AppShell
      title={t('agentEditor.title')}
      subtitle={t('agentEditor.subtitle')}
      user={user}
      onLogout={handleLogout}
      actions={
        <>
          <Link to={`/workspaces/${workspaceId}/content/new`} className="btn btn-small">
            {t('agentEditor.actions.openEditor')}
          </Link>
          <Link to={`/workspaces/${workspaceId}/content`} className="btn btn-secondary btn-small">
            {t('common.toPosts')}
          </Link>
        </>
      }
    >
      <AgentSectionLayout workspaceId={workspaceId} activeItem="editor">
        <div style={{ display: 'grid', gap: '1rem' }}>
          <div className="card">
            <h2 style={{ marginTop: 0 }}>{t('agentEditor.about.title')}</h2>
            <p>
              {t('agentEditor.about.text')}
            </p>
          </div>

          <div
            style={{
              display: 'grid',
              gap: '1rem',
              gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            }}
          >
            <div className="card">
              <h3 style={{ marginTop: 0 }}>{t('agentEditor.newDraft.title')}</h3>
              <p className="muted">
                {t('agentEditor.newDraft.text')}
              </p>
              <Link to={`/workspaces/${workspaceId}/content/new`} className="btn btn-small">
                {t('agentEditor.newDraft.action')}
              </Link>
            </div>

            <div className="card">
              <h3 style={{ marginTop: 0 }}>{t('agentEditor.existingPosts.title')}</h3>
              <p className="muted">
                {t('agentEditor.existingPosts.text')}
              </p>
              <Link to={`/workspaces/${workspaceId}/content`} className="btn btn-secondary btn-small">
                {t('agentEditor.existingPosts.action')}
              </Link>
            </div>
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>{t('agentEditor.when.title')}</h3>
            <ul style={{ marginBottom: 0, paddingLeft: '1rem' }}>
              <li>{t('agentEditor.when.item1')}</li>
              <li>{t('agentEditor.when.item2')}</li>
              <li>{t('agentEditor.when.item3')}</li>
            </ul>
          </div>
        </div>
      </AgentSectionLayout>
    </AppShell>
  )
}
