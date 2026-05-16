import { useState, useEffect } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import { deleteJob, getJob, runJobAction } from '../adapters/jobs'
import AppShell from '../components/AppShell'
import { useI18n } from '../i18n'

const isLiveOnlyCompleted = (job) =>
  job.requested_limit === 0 && job.status === 'completed';

const isCancelled = (job) => {
  if (!job?.error_payload) return false;
  const payload = typeof job.error_payload === 'string'
    ? (() => { try { return JSON.parse(job.error_payload); } catch { return {}; } })()
    : job.error_payload;
  return payload?.code === 'VALIDATION_JOB_CANCELLED';
};

export default function JobDetail() {
  const { locale, t } = useI18n()
  const { workspaceId, jobId } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchJob = () => {
      setError('');
      getJob(workspaceId, jobId)
        .then(setJob)
        .catch((e) => {
          setJob(null);
          setError(e.message);
        })
        .finally(() => setLoading(false));
    };
    fetchJob();
    const interval = setInterval(fetchJob, 3000);
    return () => clearInterval(interval);
  }, [workspaceId, jobId]);

  const doAction = async (action) => {
    setActionLoading(true);
    try {
      const updated = await runJobAction(workspaceId, jobId, action);
      setJob(updated);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(t('jobDetail.confirmDelete'))) return;
    setActionLoading(true);
    setError('');
    try {
      await deleteJob(workspaceId, jobId);
      navigate(`/workspaces/${workspaceId}/channels`);
    } catch (e) {
      setError(e.message);
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) return <div className="container">{t('common.loading')}</div>;
  if (!job) return <div className="container"><p className="error">{error || t('jobDetail.notFound')}</p></div>;

  const canPause = ['pending', 'running'].includes(job.status);
  const canCancel = ['pending', 'running'].includes(job.status);
  const canRetry = job.status === 'failed';
  const canDelete = !(job.status === 'completed' && job.requested_limit > 0);

  const dateStr = job.created_at
    ? new Date(job.created_at).toLocaleDateString(locale === 'en' ? 'en-US' : 'ru-RU', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      })
    : '—';

  return (
    <AppShell
      title={t('jobDetail.title', { date: dateStr })}
      actions={
        <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">
          {t('common.back')}
        </Link>
      }
    >
      <div className="stack">
        {error && <p className="error">{error}</p>}
        {isLiveOnlyCompleted(job) && (
          <div className="card" style={{ borderColor: 'var(--primary)', background: 'rgba(59, 130, 246, 0.08)' }}>
            <p className="section-copy">
              {t('jobDetail.liveOnlyCompleted')}
            </p>
            <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small" style={{ marginTop: '0.5rem' }}>
              {t('jobDetail.toChannels')}
            </Link>
          </div>
        )}
        <div className="card">
          <div className="status-row">
            <span className="status-label">{t('common.status')}</span>
            <strong>{job.status}</strong>
          </div>
          {job.fetched_posts_count != null && (
            <div className="status-row">
              <span className="status-label">{t('jobDetail.fetchedPosts')}</span>
              <strong>{job.fetched_posts_count}</strong>
            </div>
          )}
          <div className="status-row">
            <span className="status-label">{t('jobDetail.processedPosts')}</span>
            <strong>{job.processed_posts}</strong>
          </div>
          <div className="inline-actions" style={{ marginTop: '1rem' }}>
            {canPause && (
              <button className="btn btn-secondary" onClick={() => doAction('pause')} disabled={actionLoading}>
                {t('common.pause')}
              </button>
            )}
            {canCancel && (
              <button className="btn btn-secondary" onClick={() => doAction('cancel')} disabled={actionLoading}>
                {t('common.cancel')}
              </button>
            )}
            {canRetry && (
              <button className="btn" onClick={() => doAction('retry')} disabled={actionLoading}>
                {t('common.retry')}
              </button>
            )}
            {canDelete && (
              <button className="btn btn-secondary" onClick={handleDelete} disabled={actionLoading}>
                {t('common.delete')}
              </button>
            )}
          </div>
        </div>

        {isCancelled(job) && (
          <div className="card" style={{ borderColor: 'var(--text-muted)', background: 'rgba(148, 163, 184, 0.08)' }}>
            <h3>{t('jobDetail.cancelled.title')}</h3>
            <p className="status-copy">{t('jobDetail.cancelled.text')}</p>
          </div>
        )}
        {job.error_payload && !isCancelled(job) && (
          <div className="card">
            <h3>{t('review.status.failed')}</h3>
            <p className="status-copy">{t('jobDetail.errorShown')}</p>
            <pre className="code-block">
              {typeof job.error_payload === 'string' ? job.error_payload : JSON.stringify(job.error_payload, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </AppShell>
  );
}
