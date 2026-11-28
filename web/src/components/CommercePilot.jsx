import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  WB, commercePilotApi, pilotDraftRoute, readPilotConnection, readPilotDraft, readPilotProduct,
  readPilotReview, readPilotSizes,
} from '../adapters/commercePilots'
import { useI18n } from '../i18n'
import './CommercePilot.css'

function errorKey(error) {
  if (error?.status === 429) return 'rateLimited'
  if (error?.status === 401 || error?.status === 403 || error?.status === 410
    || ['WB_CONNECTION_NOT_FOUND', 'YANDEX_MARKET_NOT_FOUND'].includes(error?.code)) return 'accessLost'
  if (error?.code?.includes('OUTCOME_UNKNOWN') || error?.code === 'ASSISTANT_OBJECT_RESULT_UNKNOWN') return 'outcomeUnknown'
  if (error?.status === 404) return 'notFound'
  return 'error'
}

function loadOperation(key) {
  try {
    const data = JSON.parse(sessionStorage.getItem(key) || 'null')
    return data && typeof data === 'object' ? data : null
  } catch { return null }
}

function storeOperation(key, value) {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value))
    else sessionStorage.removeItem(key)
  } catch { /* The current screen can still finish the request without browser storage. */ }
}

function ProductDraft({ workspaceId, actorId, provider, connection, client, onRevoke, onAccessLost }) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const id = useId()
  const wb = provider === WB
  const storageKey = JSON.stringify(['postbridge.pilot.draft', actorId, workspaceId, provider, connection.id, connection.version])
  const [operation, setOperation] = useState(() => loadOperation(storageKey))
  const [productId, setProductId] = useState(() => loadOperation(storageKey)?.selection?.id || '')
  const [sizes, setSizes] = useState(null)
  const [size, setSize] = useState('')
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [revoking, setRevoking] = useState(false)
  const locked = useRef(false)
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const saved = pilotDraftRoute(operation?.draft, workspaceId)
  const selectedId = productId.trim()
  const validProduct = wb ? /^[1-9][0-9]{0,17}$/.test(selectedId) : Boolean(selectedId)
  const selection = { id: selectedId, size: Number(size) }
  const savedOperation = (value) => {
    storeOperation(storageKey, value)
    if (mounted.current) setOperation(value)
  }
  const run = async (callback) => {
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setError('')
    try { await callback() } catch (failure) {
      if (mounted.current) {
        const key = errorKey(failure)
        if (key === 'accessLost') onAccessLost(connection.id)
        else setError(key)
      }
    } finally {
      locked.current = false
      if (mounted.current) setBusy(false)
    }
  }
  const clear = () => {
    savedOperation(null)
    setPreview(null)
    setSizes(null)
    setSize('')
    setProductId('')
    setError('')
  }

  const loadSizes = () => run(async () => {
    setSize('')
    setPreview(null)
    setSizes(null)
    setSizes(readPilotSizes(await client.sizes(connection.id, selection.id)))
  })

  const loadPreview = () => run(async () => {
    setPreview(null)
    setPreview(readPilotProduct(await client.preview(connection.id, selection), selection, wb))
  })

  const prepare = () => run(async () => {
    const checked = readPilotReview(await client.prepare(connection.id, selection), selection)
    if (checked.price) savedOperation(checked)
    else {
      setPreview(current => current && { ...current, prices: [] })
      setError('priceMissing')
    }
  })

  const confirm = () => run(async () => {
    const submitted = {
      ...(operation || { selection, requestKey: window.crypto.randomUUID() }),
      submitted: true,
    }
    savedOperation(submitted)
    const draft = readPilotDraft(await client.confirm(connection.id, submitted), workspaceId)
    savedOperation({ selection: submitted.selection, draft })
    if (mounted.current) navigate(draft.route)
  })

  const displayedSelection = operation?.selection || selection
  return <>
    <div className="commerce-pilot-controls">
      {revoking ? <>
        <p>{t('commercePilot.revokePrompt')}</p>
        <button type="button" className="btn btn-secondary btn-small" disabled={busy}
          onClick={() => run(async () => {
            const reply = await client.revoke(connection.id, connection.version)
            if (reply?.status !== 'revoked') throw new Error('Invalid revocation response')
            clear()
            onRevoke(connection.id)
          })}>{t('commercePilot.disconnect')}</button>
        <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => setRevoking(false)}>
          {t('common.cancel')}
        </button>
      </> : <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => setRevoking(true)}>
        {t('commercePilot.disconnect')}
      </button>}
    </div>
    {error && <p role="alert" className="error">{t(`commercePilot.${error}`)}</p>}
    {busy && <p role="status" className="muted">{t('common.loading')}</p>}
    {saved ? <div className="commerce-pilot-controls">
      <Link to={saved.route} className="btn">{t('commercePilot.openDraft')}</Link>
      <button type="button" className="btn btn-secondary" disabled={busy} onClick={clear}>{t('commercePilot.anotherDraft')}</button>
    </div> : <>
      {!operation && <fieldset disabled={busy || revoking} className="commerce-pilot-fields">
        <label htmlFor={`${id}-product`}>{t(wb ? 'commercePilot.article' : 'commercePilot.offer')}</label>
        <input id={`${id}-product`} inputMode={wb ? 'numeric' : undefined} maxLength={wb ? 18 : 255}
          value={productId} onChange={event => { setProductId(event.target.value); setPreview(null); setSizes(null); setSize(''); setError('') }} />
        {wb && <button type="button" className="btn btn-secondary" disabled={!validProduct} onClick={loadSizes}>
          {t('commercePilot.findSizes')}
        </button>}
        {wb && sizes?.length > 0 && <>
          <label htmlFor={`${id}-size`}>{t('commercePilot.size')}</label>
          <select id={`${id}-size`} value={size} onChange={event => { setSize(event.target.value); setPreview(null); setError('') }}>
            <option value="">{t('commercePilot.chooseSize')}</option>
            {sizes.map(row => <option key={row.size_id} value={row.size_id}>{row.label ? `${row.label} · ${row.size_id}` : row.size_id}</option>)}
          </select>
        </>}
        {wb && sizes?.length === 0 && <p>{t('commercePilot.noSizes')}</p>}
        <button type="button" className="btn btn-secondary" disabled={!validProduct || (wb && !size)} onClick={loadPreview}>
          {t('commercePilot.preview')}
        </button>
      </fieldset>}
      {(preview || operation) && <section className="commerce-pilot-product">
        {preview && <h3>{preview.title}</h3>}
        <p>{t(wb ? 'commercePilot.wbSelection' : 'commercePilot.marketSelection', displayedSelection)}</p>
        {(operation?.price || preview?.prices.length)
          ? <p>{t('commercePilot.price', { price: operation?.price || preview.prices.join(' · ') })}</p>
          : preview && <p className="muted">{t('commercePilot.priceMissing')}</p>}
        {operation?.summary ? <>
          <h3>{t('commercePilot.review')}</h3>
          <p className="commerce-pilot-text">{operation.summary}</p>
        </> : preview?.description && <p className="commerce-pilot-text">{preview.description}</p>}
        {operation?.submitted && !error && !busy && <p>{t('commercePilot.resume')}</p>}
        <div className="commerce-pilot-controls">
          {wb && !operation ? <button type="button" className="btn" disabled={!preview?.prices.length || busy || revoking}
            onClick={prepare}>{t('commercePilot.prepare')}</button>
            : <button type="button" className="btn" disabled={busy || revoking} onClick={confirm}>
              {t(operation?.submitted ? 'commercePilot.retry' : 'commercePilot.confirm')}
            </button>}
          {operation && !operation.submitted && <button type="button" className="btn btn-secondary" disabled={busy || revoking}
            onClick={() => { savedOperation(null); setError('') }}>{t('common.cancel')}</button>}
        </div>
      </section>}
    </>}
  </>
}

export default function CommercePilot({ pilot, workspaceId, actorId }) {
  const { t } = useI18n()
  const id = useId()
  const wb = pilot.id === WB
  const client = useMemo(() => commercePilotApi(workspaceId, pilot.id), [workspaceId, pilot.id])
  const [newIds, setNewIds] = useState([])
  const [selected, setSelected] = useState(pilot.connections[0] || '')
  const [status, setStatus] = useState(null)
  const [loadVersion, setLoadVersion] = useState(0)
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState('')
  const ids = [...new Set([...pilot.connections, ...newIds])]
  const connectionId = ids.includes(selected) ? selected : ids[0]
  const active = status?.id === connectionId ? status : null

  useEffect(() => {
    if (!connectionId) return undefined
    const controller = new AbortController()
    client.status(connectionId, controller.signal).then(value => {
      if (!controller.signal.aborted) setStatus({ id: connectionId, connection: readPilotConnection(value, connectionId, wb) })
    }).catch(error => {
      if (!controller.signal.aborted) {
        setStatus({ id: connectionId, error: error?.status === 404 ? 'accessLost' : errorKey(error) })
      }
    })
    return () => controller.abort()
  }, [client, connectionId, loadVersion, wb])

  const connect = async (event) => {
    event.preventDefault()
    if (connecting || !pilot.canConnect) return
    const form = event.currentTarget
    const fields = new FormData(form)
    const token = String(fields.get('credential') || '').trim()
    const campaign = String(fields.get('campaign') || '').trim()
    form.reset()
    if (!token) return
    setConnecting(true)
    setConnectError('')
    try {
      const connection = readPilotConnection(await client.connect(token, campaign), null, wb)
      setNewIds(current => [...current, connection.id])
      setSelected(connection.id)
      setStatus({ id: connection.id, connection })
    } catch (error) {
      setConnectError(errorKey(error))
    } finally {
      setConnecting(false)
    }
  }

  const connection = active?.connection
  return <section className="connector-detail-section commerce-pilot" aria-labelledby={`${id}-pilot`}>
    <h2 id={`${id}-pilot`}>{t('commercePilot.title')}</h2>
    {ids.length > 1 && <div className="commerce-pilot-fields">
      <label htmlFor={`${id}-connection`}>{t('commercePilot.connection')}</label>
      <select id={`${id}-connection`} value={connectionId} disabled={connecting}
        onChange={event => setSelected(event.target.value)}>
        {ids.map(value => <option key={value} value={value}>{value}</option>)}
      </select>
    </div>}
    {connectionId && !active && <p role="status">{t('common.loading')}</p>}
    {active?.error && <div className="commerce-pilot-controls">
      <p role="alert" className="error">{t(`commercePilot.${active.error}`)}</p>
      <button type="button" className="btn btn-secondary btn-small" onClick={() => {
        setStatus(null)
        setLoadVersion(v => v + 1)
      }}>{t('commercePilot.reloadConnection')}</button>
    </div>}
    {connection && <p>{connection.name || connection.account_name || connectionId} · {t(`commercePilot.status.${connection.status}`)}</p>}
    {connection?.status === 'connected' && <ProductDraft key={JSON.stringify([connection.id, connection.version])}
      workspaceId={workspaceId} actorId={actorId} provider={pilot.id} connection={connection} client={client}
      onAccessLost={lostId => setStatus({ id: lostId, error: 'accessLost' })}
      onRevoke={revokedId => setStatus(current => current?.id === revokedId
        ? { id: revokedId, connection: { ...current.connection, status: 'revoked' } } : current)} />}
    {pilot.canConnect && (!connectionId || (connection && connection.status !== 'connected')) && (
      <form onSubmit={connect} className="commerce-pilot-fields" autoComplete="off">
        {!wb && <>
          <label htmlFor={`${id}-campaign`}>{t('commercePilot.campaign')}</label>
          <input id={`${id}-campaign`} name="campaign" inputMode="numeric" pattern="[0-9]{1,19}" maxLength={19} required disabled={connecting} />
        </>}
        <label htmlFor={`${id}-credential`}>{t('commercePilot.credential')}</label>
        <input id={`${id}-credential`} name="credential" type="password" maxLength={4096} required disabled={connecting} />
        {connectError && <p role="alert" className="error">{t(`commercePilot.${connectError}`)}</p>}
        <button type="submit" className="btn" disabled={connecting}>{t('commercePilot.connect')}</button>
      </form>
    )}
  </section>
}
