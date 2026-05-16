import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { clearToken } from '../adapters/sessionToken'
import { listAdminWorkspaces } from '../adapters/admin'
import { useAuth } from '../useAuth'
import AppShell from '../components/AppShell'
import { useI18n } from '../i18n'

export default function Admin() {
  const { t } = useI18n()
  const { user } = useAuth();
  const navigate = useNavigate();
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    listAdminWorkspaces()
      .then((data) => setWorkspaces(data.items || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [navigate]);

  const handleLogout = () => {
    clearToken();
    navigate('/');
  };

  if (!user?.is_platform_admin) {
    return (
      <div className="container" style={{ paddingTop: '2rem' }}>
        <p className="error">{t('admin.forbidden')}</p>
        <Link to="/">{t('common.home')}</Link>
      </div>
    );
  }

  return (
    <AppShell
      title={t('admin.title')}
      subtitle={t('admin.subtitle')}
      user={user}
      onLogout={handleLogout}
      showAdminLink
    >
      {loading && <p className="muted">{t('common.loading')}</p>}
      {!loading && error && <p className="error">{error}</p>}
      {!loading && (
        <div className="card">
          <h3>{t('admin.workspaces.title')}</h3>
          <div className="jobs-list">
            {workspaces.map((ws) => (
              <div key={ws.id} className="jobs-list-item" style={{ display: 'grid', gap: '0.6rem' }}>
                <Link to={`/workspaces/${ws.id}/channels`} className="jobs-list-link">
                  <strong>{ws.name}</strong>
                  <span className="list-meta">{ws.id}</span>
                </Link>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                  <Link to={`/workspaces/${ws.id}/agents/ops`} className="btn btn-secondary btn-small">
                    {t('admin.actions.agentOps')}
                  </Link>
                  <Link to={`/workspaces/${ws.id}/agents/topic-scout`} className="btn btn-secondary btn-small">
                    {t('agents.nav.topicScout')}
                  </Link>
                  <Link to={`/workspaces/${ws.id}/agents/candidates`} className="btn btn-secondary btn-small">
                    {t('agents.nav.candidates')}
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </AppShell>
  );
}
