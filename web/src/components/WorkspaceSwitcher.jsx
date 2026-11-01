import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth } from '../useAuth'
import { useI18n } from '../i18n'
import { workspaceSwitchPath } from '../adapters/workspace'
import { useWorkspaceNavigation } from './WorkspaceNavigation'

export default function WorkspaceSwitcher({ workspaceId }) {
  const { user } = useAuth()
  const { t } = useI18n()
  const location = useLocation()
  const { requestSwitch, busy } = useWorkspaceNavigation()
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const triggerRef = useRef(null)
  const workspaces = user?.workspaces || []
  const current = workspaces.find((workspace) => workspace.id === workspaceId)
  useEffect(() => { setOpen(false) }, [location.pathname])
  useEffect(() => {
    if (!open) return
    const close = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false) }
    const escape = (event) => {
      if (event.key === 'Escape') { setOpen(false); triggerRef.current?.focus() }
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape) }
  }, [open])
  if (!workspaceId || !workspaces.length) return null
  const select = (workspace, path) => {
    setOpen(false)
    requestSwitch(path || workspaceSwitchPath(location.pathname, workspace.id), workspace.name)
  }
  return (
    <div className="workspace-switcher" ref={rootRef}>
      <button type="button" ref={triggerRef} className="workspace-switcher-trigger" aria-expanded={open}
        aria-controls="workspace-options" disabled={busy} title={busy ? t('workspace.busy') : t('workspace.switch')}
        onClick={() => setOpen((value) => !value)}>
        <span className="workspace-avatar" aria-hidden="true">{(current?.name || 'W').slice(0, 1).toUpperCase()}</span>
        <span className="workspace-current-name">{current?.name || t('workspace.switch')}</span>
        <span aria-hidden="true">⌄</span>
      </button>
      {open && <div className="workspace-options" id="workspace-options" aria-label={t('workspace.switch')}
        style={{ '--workspace-menu-top': `${rootRef.current?.getBoundingClientRect().bottom + 8}px` }}>
        <p className="workspace-options-label">{t('workspace.title')}</p>
        {workspaces.map((workspace) => (
          <button type="button" className={`workspace-option${workspace.id === workspaceId ? ' is-current' : ''}`}
            key={workspace.id} aria-current={workspace.id === workspaceId ? 'true' : undefined}
            onClick={() => workspace.id === workspaceId ? setOpen(false) : select(workspace)}>
            <span className="workspace-option-copy"><strong>{workspace.name}</strong>
              <span>{workspace.plan_code ? workspace.plan_code.charAt(0).toUpperCase() + workspace.plan_code.slice(1) : 'Free'} · {t('workspace.role.owner')}</span>
            </span>
            {workspace.id === workspaceId && <span aria-hidden="true">✓</span>}
          </button>
        ))}
        {current && <button type="button" className="workspace-settings-link" onClick={() => select(current, `/workspaces/${encodeURIComponent(current.id)}/settings`)}>{t('workspace.settings')}</button>}
      </div>}
    </div>
  )
}
