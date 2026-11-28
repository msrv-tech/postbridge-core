import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useI18n } from '../i18n'
import { useWorkspaceSwitchGuard } from './WorkspaceNavigation'
import { CommerceActionCard, CommerceResult, CommerceVideoPreview } from './CommerceMarketerResult'
import './CommerceMarketer.css'

const emptyThread = { conversationId: null, messages: [], product: null }
const responseRoles = new Set(['user', 'assistant'])
const isText = v => typeof v === 'string' && v.trim() !== ''
const nonemptyId = v => typeof v === 'string' && v.length > 0

function readHistory(value) {
  if (!Array.isArray(value?.messages) || (value.conversation_id != null && !nonemptyId(value.conversation_id))) {
    throw new Error('Invalid assistant history')
  }
  return {
    conversationId: value.conversation_id || null,
    messages: value.messages.filter(item => responseRoles.has(item?.role)),
  }
}

function readProduct(value) {
  if (!nonemptyId(value?.id) || !isText(value.title)) throw new Error('Invalid product selection')
  return value
}

function pendingCards(key) {
  try {
    const stored = JSON.parse(sessionStorage.getItem(key))
    return Array.isArray(stored) ? stored.filter(id => typeof id === 'string') : []
  } catch { return [] }
}

function FactSummary({ product }) {
  const { t } = useI18n()
  if (!product) return null
  const price = product.price
  const amount = typeof price?.amount === 'number' && Number.isFinite(price.amount)
    || typeof price?.amount === 'string' && /^[0-9]+(?:\.[0-9]+)?$/.test(price.amount)
  const knownPrice = price?.status === 'reported' && amount && /^[A-Z]{3}$/.test(price.currency)
  const knownStock = product.stock?.status === 'reported' && Number.isFinite(product.stock.quantity)
  return <section className="commerce-marketer-result" aria-label={t('commerceMarketer.selected')}>
    <strong>{product.title}</strong>
    <p>{[product.id, product.variant_label || product.variant_id].filter(Boolean).join(' · ')}</p>
    <dl className="commerce-marketer-facts">
      <dt>{t(price?.context === 'shopBasePrice' ? 'commerceMarketer.shopBasePrice' : 'commerceMarketer.price')}</dt>
      <dd>{knownPrice ? `${price.amount} ${price.currency}` : t('commerceMarketer.unknown')}</dd>
      <dt>{t(product.stock?.context === 'shopTotalStock' ? 'commerceMarketer.shopTotalStock' : 'commerceMarketer.stock')}</dt>
      <dd>{knownStock ? product.stock.quantity : t('commerceMarketer.unknown')}</dd>
    </dl>
  </section>
}

function ProductPicker({ candidates, selected, search, searchLabel, select, busy, clear, canSelect, provider }) {
  const { t } = useI18n()
  const id = useId()
  const [query, setQuery] = useState('')
  return <>
    {canSelect && <form className="commerce-marketer-form" onSubmit={event => {
      event.preventDefault()
      if (!query.trim() || busy) return
      if (search) search(query.trim())
      else select({ id: query.trim(), title: query.trim() })
    }}>
      <label htmlFor={`${id}-query`}>{t(searchLabel || (search ? 'commerceMarketer.productSearch' : `commerceMarketer.productId.${provider}`))}</label>
      <div className="commerce-marketer-inline">
        <input id={`${id}-query`} value={query} maxLength={255} disabled={busy} onChange={e => setQuery(e.target.value)} />
        <button type="submit" className="btn btn-secondary" disabled={busy || !query.trim()}>{t(search ? 'commerceMarketer.find' : 'commerceMarketer.selectProduct')}</button>
      </div>
    </form>}
    {candidates && (candidates.length ? <ul className="commerce-marketer-choices">
      {candidates.map(item => <li key={JSON.stringify([item.id, item.variant_id])}>
        <span>{item.title}{item.variant_label ? ` · ${item.variant_label}` : ''}</span>
        <button type="button" className="btn btn-secondary btn-small" onClick={() => select(item)} disabled={busy}>
          {t(item.requires_variant ? 'commerceMarketer.chooseVariant' : 'commerceMarketer.selectProduct')}
        </button>
      </li>)}
    </ul> : <p>{t('commerceMarketer.noProducts')}</p>)}
    <FactSummary product={selected} />
    {canSelect && <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={clear}>
      {t('commerceMarketer.clearSelection')}
    </button>}
  </>
}

function Conversation({ workspaceId, actorId, binding, transport, onBusy }) {
  const { t } = useI18n()
  const id = useId()
  const storage = JSON.stringify(['postbridge.marketer.cards', actorId, workspaceId, binding.id])
  const [thread, setThread] = useState(emptyThread)
  const [question, setQuestion] = useState('')
  const [candidates, setCandidates] = useState(null)
  const [initializing, setInitializing] = useState(true)
  const [historyReady, setHistoryReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [denied, setDenied] = useState(false)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(() => pendingCards(storage))
  const [videoResults, setVideoResults] = useState({})
  const active = useRef(false)
  const inProgress = useRef(false)
  const pending = useRef(null)
  const productIds = useRef(undefined)

  const reportError = (failure, historyRequest = false) => {
    if (!active.current) return
    if ([401, 403, 410].includes(failure?.status) || (historyRequest && failure?.status === 404)) {
      setDenied(true)
      setThread(emptyThread)
      setCandidates(null)
      setVideoResults({})
      pending.current = null
      setQuestion('')
      setError('access')
    } else {
      setError(failure?.status === 429 ? 'rateLimit' : 'error')
    }
  }

  useWorkspaceSwitchGuard({ busy, dirty: Boolean(question.trim()) })

  useEffect(() => {
    active.current = true
    const controller = new AbortController()
    queueMicrotask(async () => {
      if (controller.signal.aborted) return
      try {
        const history = readHistory(await transport.history({ signal: controller.signal }))
        if (!controller.signal.aborted) {
          setThread(history)
          setHistoryReady(true)
        }
      } catch (failure) {
        if (!controller.signal.aborted) reportError(failure, true)
      } finally {
        if (!controller.signal.aborted) setInitializing(false)
      }
    })
    return () => {
      active.current = false
      controller.abort()
      onBusy(false)
    }
  }, [transport, onBusy])

  const run = async (work, historyRequest = false) => {
    if (inProgress.current || denied || initializing) return
    inProgress.current = true
    setBusy(true)
    onBusy(true)
    setError('')
    try { await work() } catch (failure) { reportError(failure, historyRequest) }
    finally {
      inProgress.current = false
      if (active.current) {
        setBusy(false)
        onBusy(false)
      }
    }
  }

  const refresh = () => run(async () => {
    const history = readHistory(await transport.history({ conversationId: thread.conversationId }))
    if (active.current) {
      if (pending.current) pending.current.refreshAfterReplay = true
      setThread(current => ({ ...current, ...history }))
      setHistoryReady(true)
    }
  }, true)

  const search = (query) => run(async () => {
    const results = await transport.search(query)
    if (!Array.isArray(results)) throw new Error('Invalid product list')
    if (active.current) setCandidates(results.map(readProduct))
  })

  const select = (item) => run(async () => {
    if (item.requires_variant) {
      const items = await transport.variants(item)
      if (!Array.isArray(items)) throw new Error('Invalid variant list')
      if (active.current) setCandidates(items.map(readProduct))
      return
    }
    const product = readProduct(await transport.select(item))
    if (active.current) {
      productIds.current = [product.id]
      setCandidates(null)
      setThread(current => ({ ...current, product }))
    }
  })

  const send = (event) => {
    event.preventDefault()
    if (!historyReady || !question.trim()) return
    run(async () => {
      if (pending.current?.payload.question !== question.trim()) {
        pending.current = { payload: { question: question.trim(), conversation_id: thread.conversationId, request_key: crypto.randomUUID(),
          product_ids: productIds.current } }
      }
      const { payload: turn, refreshAfterReplay } = pending.current
      const reply = await transport.send(turn)
      if (!nonemptyId(reply?.conversation_id) || !isText(reply.message)) throw new Error('Invalid assistant reply')
      if (!active.current) return
      if (refreshAfterReplay) {
        const history = readHistory(await transport.history({ conversationId: reply.conversation_id }))
        if (!active.current) return
        setThread(current => ({ ...current, ...history }))
      } else {
        setThread(current => ({
          ...current,
          conversationId: reply.conversation_id,
          messages: [...current.messages,
            { role: 'user', message: turn.question, id: `${turn.request_key}-user` },
            { ...reply, role: 'assistant', id: `${turn.request_key}-assistant` }],
        }))
      }
      pending.current = null
      productIds.current = undefined
      setQuestion('')
    })
  }

  const apply = (card) => {
    const cardId = String(card.id)
    if (!historyReady || uncertain.includes(cardId)) return
    run(async () => {
      const marked = [...new Set([...pendingCards(storage), ...uncertain, cardId])]
      setUncertain(marked)
      // A lost response leaves only readback available for this card in this tab.
      try { sessionStorage.setItem(storage, JSON.stringify(marked)) } catch { /* Memory still locks this card. */ }
      const result = await transport.apply(cardId)
      if (!result || typeof result !== 'object') throw new Error('Invalid action result')
      if (active.current) setThread(current => ({ ...current, messages: current.messages.map(row =>
        String(row.video_action?.id) === cardId
          ? { ...row, ...result, video_action: { ...row.video_action, status: 'applied' } }
          : row) }))
    })
  }

  const readVideo = (cardId) => run(async () => {
    const result = await transport.result(cardId)
    if (!isText(result?.status)) throw new Error('Invalid video result')
    if (active.current) setVideoResults(current => ({ ...current, [cardId]: result }))
  })

  if (initializing) return <p role="status">{t('common.loading')}</p>
  if (denied) return <p role="alert" className="error">{t('commerceMarketer.access')}</p>

  return <>
    <ProductPicker candidates={candidates} selected={thread.product} busy={!historyReady || busy || Boolean(pending.current)}
      provider={binding.provider}
      search={transport.search ? search : null} searchLabel={transport.searchLabel && `commerceMarketer.${transport.searchLabel}`}
      select={select} canSelect={Boolean(transport.select)} clear={() => {
        productIds.current = []
        setCandidates(null)
        setThread(current => ({ ...current, product: null }))
      }} />
    <div role="log" aria-label={t('commerceMarketer.history')} className="commerce-marketer-history" aria-busy={busy}>
      {thread.messages.map((row, index) => <div key={row.id || index} className={`commerce-marketer-message is-${row.role}`}>
        <strong>{row.role === 'user' ? t('commerceMarketer.you') : t('commerceMarketer.title')}</strong>
        {isText(row.message) && <p className="commerce-marketer-text">{row.message}</p>}
        {row.role === 'assistant' && <>
          <CommerceActionCard card={row.video_action} disabled={!historyReady || busy}
            uncertain={uncertain.includes(String(row.video_action?.id))}
            onApply={transport.apply && apply} />
          <CommerceResult workspaceId={workspaceId} binding={binding} response={row} />
          {row.video_action?.kind === 'video_generation'
            && (row.video_action.status === 'applied' || uncertain.includes(String(row.video_action.id)))
            && <CommerceVideoPreview result={videoResults[row.video_action.id]} disabled={busy}
              onRead={() => readVideo(row.video_action.id)} />}
        </>}
      </div>)}
    </div>
    {error && <p role="alert" className="error">{t(`commerceMarketer.${error}`)}</p>}
    <form onSubmit={send} className="commerce-marketer-form">
      <label htmlFor={`${id}-question`}>{t('commerceMarketer.question')}</label>
      <textarea id={`${id}-question`} rows="3" maxLength={8000} value={question} disabled={!historyReady || busy} onChange={event => setQuestion(event.target.value)} />
      <div className="commerce-marketer-inline">
        <button type="submit" className="btn" disabled={!historyReady || !question.trim() || busy}>{t('commerceMarketer.send')}</button>
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={refresh}>{t('commerceMarketer.refresh')}</button>
      </div>
    </form>
  </>
}

export default function CommerceMarketer({ workspaceId, actorId, bindings, connect, selected, onSelect }) {
  const { t } = useI18n()
  const id = useId()
  const [busy, setBusy] = useState(false)
  const binding = selected ? bindings.find(item => item.id === selected) : bindings[0]
  const transport = useMemo(() => binding ? connect(binding) : null, [connect, binding])
  if (!binding || !transport) return <p role="alert">{t('commerceMarketer.access')}</p>

  return <section className="connector-detail-section commerce-marketer" aria-labelledby={`${id}-title`}>
    <h2 id={`${id}-title`}>{t('commerceMarketer.title')}</h2>
    {bindings.length > 1 ? <div className="commerce-marketer-form">
      <label htmlFor={`${id}-store`}>{t('commerceMarketer.store')}</label>
      <select id={`${id}-store`} value={binding.id} disabled={busy} onChange={event => onSelect(event.target.value)}>
        {bindings.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
    </div> : <p>{binding.name}</p>}
    <Conversation key={JSON.stringify([workspaceId, actorId, binding.id])} workspaceId={workspaceId} actorId={actorId}
      binding={binding} transport={transport} onBusy={setBusy} />
  </section>
}
