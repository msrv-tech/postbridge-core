import PlatformLogo from '../components/PlatformLogo'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import AppShell from '../components/AppShell'
import { listMyMetaConnections } from '../adapters/channels'
import { clearToken, getToken } from '../adapters/sessionToken'
import { BILLING_SUPPORT_EMAIL } from '../billingSupport'
import { useAuth } from '../useAuth'
import { useI18n } from '../i18n'

const emptyView = (owner, token) => ({ owner, token, items: [], next: null, confirmed: false, loading: false, error: '' })

export default function MetaConnections() {
  const { user } = useAuth()
  const { t } = useI18n()
  const navigate = useNavigate()
  const owner = user?.user_id || user?.id || ''
  const token = getToken()
  const ownerRef = useRef(owner)
  ownerRef.current = owner
  const request = useRef(0)
  const abort = useRef(null)
  const cursors = useRef(new Set())
  const [view, setView] = useState(() => emptyView(owner, token))
  const [copyState, setCopyState] = useState({ owner: '', token: '', id: '', error: false })
  const current = view.owner === owner && view.token === token ? view : emptyView(owner, token)

  const load = useCallback(async (cursor = null) => {
    const version = ++request.current
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    const isCurrent = () => version === request.current && ownerRef.current === owner && getToken() === token
    if (cursor === null) cursors.current = new Set()
    setView(old => ({ ...(old.owner === owner && old.token === token ? old : emptyView(owner, token)), loading: true, error: '' }))
    try {
      const data = await listMyMetaConnections({ cursor, signal: controller.signal })
      if (!isCurrent()) return
      if (cursor !== null && cursors.current.has(cursor)) throw new Error('Repeated inventory cursor')
      if (data.next_cursor !== null && (data.next_cursor === cursor || cursors.current.has(data.next_cursor))) throw new Error('Repeated inventory cursor')
      if (cursor !== null) cursors.current.add(cursor)
      setView(old => {
        const rows = cursor !== null && old.owner === owner && old.token === token ? old.items : []
        const byId = new Map(rows.map(item => [item.id, item]))
        data.items.forEach(item => byId.set(item.id, item))
        return { owner, token, items: [...byId.values()], next: data.next_cursor, confirmed: true, loading: false, error: '' }
      })
    } catch (failure) {
      if (!isCurrent() || failure.name === 'AbortError') return
      if (failure.status === 401) {
        setView(emptyView(owner, token))
        clearToken()
        return
      }
      if (failure.status === 403) {
        setView({ ...emptyView(owner, token), error: 'forbidden' })
        return
      }
      setView(old => ({ ...(old.owner === owner && old.token === token ? old : emptyView(owner, token)), loading: false,
        error: failure.code === 'META_OWNER_INVENTORY_INVALID' ? 'invalid' : 'unavailable' }))
    }
  }, [owner, token])

  useEffect(() => {
    setView(emptyView(owner, token))
    setCopyState({ owner: '', token: '', id: '', error: false })
    if (owner && token) load()
    return () => { ++request.current; abort.current?.abort() }
  }, [owner, token, load])

  const copyId = async (id) => {
    try {
      await navigator.clipboard.writeText(id)
      if (ownerRef.current === owner && getToken() === token) setCopyState({ owner, token, id, error: false })
    } catch {
      if (ownerRef.current === owner && getToken() === token) setCopyState({ owner, token, id, error: true })
    }
  }
  const logout = () => { clearToken(); navigate('/login') }

  return <AppShell title={t('metaConnections.title')} subtitle={t('metaConnections.intro')} user={user} onLogout={logout} workspaceId={null}>
    <div className="inline-actions" style={{ marginBottom: '1rem', flexWrap: 'wrap' }}>
      <button className="btn btn-secondary btn-small" type="button" onClick={() => load()} disabled={current.loading}>
        {t('metaConnections.refresh')}
      </button>
      <a href={`mailto:${BILLING_SUPPORT_EMAIL}`} className="btn btn-secondary btn-small">{t('common.support')}</a>
      <button className="btn btn-secondary btn-small" type="button" onClick={logout}>{t('settings.logout')}</button>
    </div>
    {current.loading && <p role="status" className="muted">{t('metaConnections.loading')}</p>}
    {current.error && <div className="card" role="alert" style={{ marginBottom: '1rem' }}>
      <p>{t(`metaConnections.error.${current.error}`)}</p>
      {current.confirmed && current.items.length > 0 && <p className="muted">{t('metaConnections.previous')}</p>}
    </div>}
    {!current.loading && !current.error && current.confirmed && current.items.length === 0
      && <div className="card empty-state"><p>{t('metaConnections.empty')}</p></div>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 19rem), 1fr))', gap: '1rem' }}>
      {current.items.map(item => <article className="card" data-meta-connection-id={item.id} key={item.id} style={{ minWidth: 0 }}>
        <div style={{ marginBottom: '1rem' }}><PlatformLogo platform={item.platform} /></div>
        <p className="muted">{item.platform === 'facebook' ? 'Facebook' : 'Instagram'}</p>
        <h2 style={{ fontSize: '1.1rem', overflowWrap: 'anywhere' }}>{item.title || item.platform_channel_id || (item.platform === 'facebook' ? 'Facebook' : 'Instagram')}</h2>
        <p><strong>{t(`metaConnections.state.${item.state}`)}</strong></p>
        <p className="muted">{t(item.state === 'reconnect_required' ? 'metaConnections.reconnectHint' : 'metaConnections.inventoryHint')}</p>
        <details>
          <summary>{t('metaConnections.details')}</summary>
          <dl style={{ overflowWrap: 'anywhere' }}>
            <dt>{t('metaConnections.nativeId')}</dt><dd style={{ margin: '0.25rem 0 0.75rem' }}>{item.id}</dd>
            <dt>{t('metaConnections.assetId')}</dt><dd style={{ margin: '0.25rem 0 0.75rem' }}>{item.platform_channel_id || '—'}</dd>
          </dl>
          <button type="button" className="btn btn-secondary btn-small" style={{ maxWidth: '100%', overflowWrap: 'anywhere' }} onClick={() => copyId(item.id)}>{t('metaConnections.copyId')}</button>
          {copyState.owner === owner && copyState.token === token && copyState.id === item.id
            && <p role="status">{t(copyState.error ? 'metaConnections.copyFailed' : 'metaConnections.copied')}</p>}
        </details>
      </article>)}
    </div>
    {current.next !== null && <button type="button" className="btn btn-secondary" style={{ marginTop: '1rem' }}
      disabled={current.loading} onClick={() => load(current.next)}>{t('metaConnections.more')}</button>}
  </AppShell>
}
