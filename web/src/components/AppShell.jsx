import { useEffect, useMemo, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { listMediaGenerationJobs } from '../adapters/media'
import { LanguageSelect, useI18n } from '../i18n'

const dismissedImageJobsKey = (workspaceId) => `postbridge_dismissed_image_jobs_${workspaceId}`

function readDismissedImageJobs(workspaceId) {
  if (typeof window === 'undefined' || !workspaceId) return new Set()
  try {
    const raw = window.localStorage.getItem(dismissedImageJobsKey(workspaceId))
    const list = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(list) ? list : [])
  } catch {
    return new Set()
  }
}

function writeDismissedImageJobs(workspaceId, ids) {
  if (typeof window === 'undefined' || !workspaceId) return
  window.localStorage.setItem(dismissedImageJobsKey(workspaceId), JSON.stringify([...ids].slice(-50)))
}

export default function AppShell({
  title,
  subtitle,
  user,
  onLogout,
  children,
  actions,
  showAdminLink = false,
  workspaceId: workspaceIdProp = '',
}) {
  const location = useLocation()
  const { t } = useI18n()
  const workspaceMatch = location.pathname.match(/\/workspaces\/([^/]+)/)
  const workspaceFromPath = workspaceMatch?.[1] || ''
  const workspaceFromQuery = new URLSearchParams(location.search).get('workspace') || ''
  const workspaceId = workspaceIdProp || workspaceFromPath || workspaceFromQuery
  const contentHref = workspaceId ? `/workspaces/${workspaceId}/content` : '/'
  const channelsHref = workspaceId ? `/workspaces/${workspaceId}/channels` : '/'
  const agentsHref = workspaceId ? `/workspaces/${workspaceId}/agents/topic-scout` : '/'
  const settingsHref = workspaceId ? `/workspaces/${workspaceId}/settings` : '/settings'
  const brandHref = '/home'
  const [imageJobs, setImageJobs] = useState([])
  const [dismissedImageJobs, setDismissedImageJobs] = useState(() => readDismissedImageJobs(workspaceId))

  const isContentActive = location.pathname.includes('/content')
  const isChannelsActive = location.pathname.includes('/channels')
  const isAgentsActive = location.pathname.includes('/agents/')
  const isSettingsActive = location.pathname.includes('/settings')
  const isAdminActive = location.pathname === '/admin' || location.pathname.includes('/agents/ops')

  const navClassName = (active) => `app-nav-link${active ? ' is-active' : ''}`
  const visibleImageJobs = useMemo(
    () =>
      imageJobs.filter((job) => {
        if (!job?.id) return false
        if (job.status === 'pending' || job.status === 'running') return true
        return !dismissedImageJobs.has(job.id)
      }),
    [dismissedImageJobs, imageJobs],
  )

  useEffect(() => {
    setDismissedImageJobs(readDismissedImageJobs(workspaceId))
  }, [workspaceId])

  useEffect(() => {
    if (!workspaceId) {
      setImageJobs([])
      return undefined
    }
    let cancelled = false
    const load = async () => {
      try {
        const result = await listMediaGenerationJobs(workspaceId, { limit: 5 })
        if (!cancelled) setImageJobs(result.items || [])
      } catch {
        if (!cancelled) setImageJobs([])
      }
    }
    load()
    const interval = setInterval(load, 5000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [workspaceId])

  const dismissImageJob = (jobId) => {
    setDismissedImageJobs((current) => {
      const next = new Set(current)
      next.add(jobId)
      writeDismissedImageJobs(workspaceId, next)
      return next
    })
  }
  const postHrefForImageJob = (job) =>
    workspaceId && job?.content_item_id ? `/workspaces/${workspaceId}/content/${job.content_item_id}` : ''

  return (
    <div className="app-shell">
      <div className="container app-shell-inner">
        <header className="app-header">
          <div className="app-header-top">
            <div className="app-header-top-left">
              <Link to={brandHref} className="brand brand-small">
                <img src="/postbridge-mark.svg" alt="" className="brand-mark" width="26" height="26" />
                Postbridge
              </Link>
              <nav className="app-nav" aria-label={t('app.nav.aria')}>
                <NavLink to={contentHref} className={navClassName(isContentActive)}>
                  {t('app.nav.content')}
                </NavLink>
                <NavLink to={channelsHref} className={navClassName(isChannelsActive)}>
                  {t('app.nav.channels')}
                </NavLink>
                <NavLink to={agentsHref} className={navClassName(isAgentsActive)}>
                  {t('app.nav.agents')}
                </NavLink>
                <NavLink to={settingsHref} className={navClassName(isSettingsActive)}>
                  {t('app.nav.settings')}
                </NavLink>
                {showAdminLink && (
                  <NavLink to="/admin" className={navClassName(isAdminActive)}>
                    {t('app.nav.admin')}
                  </NavLink>
                )}
                <LanguageSelect compact />
              </nav>
            </div>
            <div className="app-header-top-right">
              {actions && <div className="app-header-actions">{actions}</div>}
            </div>
          </div>
          <div className="app-header-main">
            <h1 className="page-title">{title}</h1>
            {subtitle && <p className="page-subtitle">{subtitle}</p>}
          </div>
        </header>

        {children}
      </div>
      {visibleImageJobs.length > 0 && (
        <div className="image-job-notifications" aria-live="polite">
          <div className="image-job-notifications-title">{t('imageJobs.title')}</div>
          {visibleImageJobs.map((job) => (
            <div key={job.id} className={`image-job-card is-${job.status}`}>
              <div>
                <strong>
                  {job.status === 'completed'
                    ? t('imageJobs.completed')
                    : job.status === 'failed'
                      ? t('imageJobs.failed')
                      : t('imageJobs.running')}
                </strong>
                <p>{t(job.target === 'media' ? 'imageJobs.target.media' : 'imageJobs.target.cover')}</p>
                {job.error_message && <p className="error">{job.error_message}</p>}
              </div>
              <div className="image-job-actions">
                {postHrefForImageJob(job) && (
                  <Link className="btn btn-secondary btn-small" to={postHrefForImageJob(job)}>
                    {t('imageJobs.post')}
                  </Link>
                )}
                {job.url && (
                  <a className="btn btn-secondary btn-small" href={job.url} target="_blank" rel="noreferrer">
                    {t('imageJobs.open')}
                  </a>
                )}
                {(job.status === 'completed' || job.status === 'failed') && (
                  <button type="button" className="btn btn-secondary btn-small" onClick={() => dismissImageJob(job.id)}>
                    {t('common.close')}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
