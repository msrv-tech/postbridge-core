import { NavLink } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { useI18n } from '../i18n'
import { getVideoCapabilities } from '../adapters/video'

export default function AgentSectionLayout({
  workspaceId,
  activeItem,
  children,
  sidebarFooter = null,
}) {
  const { t } = useI18n()
  const [videoEnabled, setVideoEnabled] = useState(activeItem === 'videos')
  useEffect(() => {
    let active = true
    getVideoCapabilities(workspaceId).then((caps) => { if (active) setVideoEnabled(caps.supports_schedules || activeItem === 'videos') }).catch(() => {})
    return () => { active = false }
  }, [workspaceId, activeItem])
  const links = [
    { key: 'topic-scout', label: t('agents.nav.topicScout'), to: `/workspaces/${workspaceId}/agents/topic-scout` },
    { key: 'editor', label: t('agents.nav.editor'), to: `/workspaces/${workspaceId}/agents/editor` },
    ...(videoEnabled ? [{ key: 'videos', label: t('videoSchedule.title'), to: `/workspaces/${workspaceId}/agents/videos` }] : []),
    { key: 'candidates', label: t('agents.nav.candidates'), to: `/workspaces/${workspaceId}/agents/candidates` },
    { key: 'faq', label: t('common.faq'), to: `/workspaces/${workspaceId}/agents/help` },
  ]

  return (
    <div className="agent-section-layout">
      <aside className="card agent-section-sidebar">
        <div className="agent-section-sidebar-header">
          <h2 style={{ margin: 0 }}>{t('agents.section.title')}</h2>
          <p className="muted" style={{ margin: '0.35rem 0 0 0' }}>
            {t('agents.section.subtitle')}
          </p>
        </div>

        <nav className="agent-section-nav" aria-label={t('agents.section.navAria')}>
          {links.map((link) => (
            <NavLink
              key={link.key}
              to={link.to}
              className={`agent-section-nav-link${activeItem === link.key ? ' is-active' : ''}`}
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        {sidebarFooter ? <div className="agent-section-sidebar-footer">{sidebarFooter}</div> : null}
      </aside>

      <div className="agent-section-content">{children}</div>
    </div>
  )
}
