import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useI18n } from '../i18n'
import { useAuth } from '../useAuth'
import { rememberWorkspace } from '../adapters/workspace'

const NavigationContext = createContext(null)

export function WorkspaceNavigationProvider({ children }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { t } = useI18n()
  const { user } = useAuth()
  const guards = useRef(new Map())
  const [, update] = useState(0)
  const [pending, setPending] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const dialogRef = useRef(null)
  const register = useCallback((id, guard) => {
    guards.current.set(id, guard)
    update((value) => value + 1)
    return () => { guards.current.delete(id); update((value) => value + 1) }
  }, [])
  const busy = [...guards.current.values()].some((guard) => guard.busy)
  const navigateToWorkspace = (path) => {
    const workspaceId = path.match(/^\/workspaces\/([^/]+)/)?.[1]
    if (workspaceId) rememberWorkspace(user, decodeURIComponent(workspaceId))
    navigate(path)
  }
  const requestSwitch = (path, name) => {
    if ([...guards.current.values()].some((guard) => guard.busy)) return
    const dirty = [...guards.current.values()].filter((guard) => guard.dirty)
    if (!dirty.length) { navigateToWorkspace(path); return }
    setError('')
    setPending({ path, name, dirty })
  }
  useEffect(() => { setPending(null); setError('') }, [location.pathname])
  useEffect(() => {
    if (pending && !dialogRef.current.open) dialogRef.current.showModal()
  }, [pending])
  const saveAndSwitch = async () => {
    setSaving(true)
    setError('')
    try {
      for (const guard of pending.dirty) {
        const result = await guard.onSave()
        if (result === false || result === null) throw new Error(t('workspace.saveFailed'))
      }
      navigateToWorkspace(pending.path)
      setPending(null)
    } catch (failure) { setError(failure.message || t('workspace.saveFailed')) }
    finally { setSaving(false) }
  }
  const canSave = pending?.dirty.every((guard) => guard.onSave)
  return (
    <NavigationContext.Provider value={{ register, requestSwitch, busy: busy || saving }}>
      {children}
      {pending && (
        <dialog ref={dialogRef} className="workspace-switch-dialog" aria-labelledby="workspace-switch-title"
          onCancel={(event) => { event.preventDefault(); if (!saving) setPending(null) }}>
          <h2 id="workspace-switch-title">{t('workspace.unsavedTitle')}</h2>
          <p>{t('workspace.unsavedText', { name: pending.name })}</p>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="workspace-switch-actions">
            {canSave && <button type="button" className="btn" disabled={saving} onClick={saveAndSwitch}>
              {saving ? t('workspace.saving') : pending.dirty.length === 1 && pending.dirty[0].saveLabel || t('workspace.saveAndSwitch')}
            </button>}
            <button type="button" className="btn btn-secondary" disabled={saving} onClick={() => { navigateToWorkspace(pending.path); setPending(null) }}>{t('workspace.discardAndSwitch')}</button>
            <button type="button" className="btn btn-secondary" disabled={saving} onClick={() => setPending(null)}>{t('workspace.stay')}</button>
          </div>
        </dialog>
      )}
    </NavigationContext.Provider>
  )
}

export function useWorkspaceNavigation() { return useContext(NavigationContext) }

export function useWorkspaceSwitchGuard({ dirty = false, busy = false, onSave, saveLabel } = {}) {
  const navigation = useWorkspaceNavigation()
  const register = navigation?.register
  const id = useId()
  const saveRef = useRef(onSave)
  saveRef.current = onSave
  const hasSave = Boolean(onSave)
  useEffect(() => register?.(id, { dirty, busy, saveLabel, onSave: hasSave ? () => saveRef.current() : null }),
    [register, id, dirty, busy, saveLabel, hasSave])
}
