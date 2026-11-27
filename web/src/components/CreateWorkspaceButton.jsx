import { useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useAuth } from '../useAuth'
import { useI18n } from '../i18n'
import { useWorkspaceNavigation } from './WorkspaceNavigation'

export default function CreateWorkspaceButton({ className, onOpen }) {
  const { createWorkspace } = useAuth()
  const { busy, requestSwitch } = useWorkspaceNavigation()
  const { t } = useI18n()
  const id = useId()
  const dialog = useRef(null)
  const request = useRef(null)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event) => {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName || busy || saving) return
    setSaving(true)
    setError('')
    try {
      if (request.current?.name !== trimmedName) {
        request.current = { name: trimmedName, id: crypto.randomUUID() }
      }
      const workspace = await createWorkspace(trimmedName, request.current.id)
      if (!workspace) return
      request.current = null
      dialog.current?.close()
      requestSwitch(`/workspaces/${encodeURIComponent(workspace.id)}/content`, workspace.name)
    } catch {
      setError(t('workspace.create.error'))
    } finally {
      setSaving(false)
    }
  }

  return <>
    <button type="button" className={className} disabled={busy} onClick={() => {
      onOpen?.()
      setName('')
      setError('')
      dialog.current.showModal()
    }}>
      {t('workspace.create.title')}
    </button>
    {createPortal(<dialog ref={dialog} className="workspace-switch-dialog" aria-labelledby={`${id}-title`}
      onCancel={(event) => { if (saving) event.preventDefault() }}>
      <h2 id={`${id}-title`}>{t('workspace.create.title')}</h2>
      <p className="muted">{t('workspace.create.freePlan')}</p>
      <form onSubmit={submit}>
        <label htmlFor={`${id}-name`}>{t('workspace.create.name')}</label>
        <input id={`${id}-name`} autoFocus required maxLength={255} value={name}
          onChange={(event) => setName(event.target.value)} disabled={saving} />
        {error && <p className="error" role="alert">{error}</p>}
        <div className="workspace-switch-actions">
          <button type="submit" className="btn" disabled={!name.trim() || saving || busy}>
            {saving ? t('workspace.create.saving') : t('workspace.create.submit')}
          </button>
          <button type="button" className="btn btn-secondary" disabled={saving} onClick={() => dialog.current.close()}>
            {t('common.cancel')}
          </button>
        </div>
      </form>
    </dialog>, document.body)}
  </>
}
