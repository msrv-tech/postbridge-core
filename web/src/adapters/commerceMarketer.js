import { api } from './apiClient'
import { commercePilotApi, readPilotProduct, readPilotSizes } from './commercePilots'

export const marketerConnectors = {
  woocommerce: { name: 'WooCommerce', connector: 'woocommerce' },
  shopify: { name: 'Shopify', connector: 'shopify' },
  ozon: { name: 'Ozon', connector: 'ozon-app' },
  wildberries: { name: 'Wildberries', connector: 'wildberries-source' },
  'yandex-market': { name: 'Yandex Market', connector: 'yandex-market-source' },
}

const part = encodeURIComponent
const list = value => Array.isArray(value?.items) ? value.items : []
const validId = id => typeof id === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(id)
const workspaceApi = workspaceId => `/api/workspaces/${part(workspaceId)}`
const nativeConnections = (workspaceId, provider) => `${workspaceApi(workspaceId)}/connectors/${provider}/connections`

export function commerceMarketerHref(workspaceId, provider, connectionId) {
  if (!workspaceId || !Object.hasOwn(marketerConnectors, provider) || (connectionId != null && !validId(connectionId))) return null
  const query = new URLSearchParams({ assistant: 'commerce' })
  if (connectionId) query.set('store', connectionId)
  return `/workspaces/${part(workspaceId)}/connectors/${marketerConnectors[provider].connector}?${query}`
}

function marketerPath(workspaceId, provider, id) {
  return `${workspaceApi(workspaceId)}/commerce-marketer/${provider}/connections/${part(id)}`
}

function read(path, signal) {
  return api(path, { signal, cache: 'no-store' })
}

function post(path, body) {
  return api(path, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) })
}

export async function discoverMarketers(workspaceId, pilots, signal) {
  const available = (result) => result.catch(() => null)
  const [woo, shopify, ozon] = await Promise.all([
    available(read(nativeConnections(workspaceId, 'woocommerce'), signal)),
    available(read(nativeConnections(workspaceId, 'shopify'), signal)),
    available(read(`/workspaces/${part(workspaceId)}/external-apps/installations`, signal)),
  ])
  if (signal.aborted) return []

  const known = [
    ...list(woo).filter(row => row?.status === 'active').map(row => ['woocommerce', row.id, row.store_url]),
    ...list(shopify).filter(row => row?.status === 'connected').map(row => ['shopify', row.id, row.shop]),
    ...list(ozon).filter(row => row?.workspace_id === workspaceId && row.app_id === 'ozon-app' && row.status === 'connected')
      .map(row => ['ozon', row.id, row.external_account_name]),
  ]
  for (const provider of ['wildberries', 'yandex-market']) {
    const pilot = pilots.find(entry => entry.id === marketerConnectors[provider].connector)
    for (const id of pilot?.connections || []) known.push([provider, id, null])
  }

  const unique = [...new Map(known.filter(([, id]) => validId(id)).map(row => [JSON.stringify(row.slice(0, 2)), row])).values()]
  return (await Promise.all(unique.map(async ([provider, id, name]) => {
    const profile = await available(read(marketerPath(workspaceId, provider, id), signal))
    if (signal.aborted || profile?.provider !== provider || profile.connection_id !== id || !Array.isArray(profile.capabilities)) return null
    return { id, provider, name: typeof name === 'string' && name.trim() ? name
      : `${profile.display_name || marketerConnectors[provider].name} · ${id}`, capabilities: profile.capabilities }
  }))).filter(Boolean)
}

function wooProduct(row) {
  const variant = Array.isArray(row?.attributes) ? row.attributes.map(item => item?.option || item?.value).filter(Boolean).join(' · ') : ''
  return { id: row?.id, title: row?.name, variant_id: row?.variation_id, variant_label: variant,
    requires_variant: row?.type === 'variable', price: row?.price, stock: row?.stock }
}

function selectedProduct(workspaceId, binding) {
  const { id, provider, capabilities } = binding
  if (!capabilities.includes('get_products')) return {}
  const path = `${nativeConnections(workspaceId, provider)}/${part(id)}`
  if (provider === 'woocommerce') return {
    search: async query => list(await read(`${path}/products?${new URLSearchParams({ search: query.slice(0, 100) })}`)).map(wooProduct),
    variants: async item => list(await read(`${path}/products/${part(item.id)}/variations`)).map(wooProduct),
    select: async item => {
      const product = wooProduct(await read(`${path}/selection/${part(item.id)}`))
      if (product.id !== item.id || product.requires_variant) throw new Error('Mismatched WooCommerce selection')
      return product
    },
  }
  if (provider === 'shopify') return {
    select: async item => {
      const selector = /^shopify:([1-9][0-9]{0,24}):([1-9][0-9]{0,24})$/.exec(item.id)
      if (!selector) throw new Error('Invalid Shopify selection')
      const product_id = `gid://shopify/Product/${selector[1]}`
      const variant_id = `gid://shopify/ProductVariant/${selector[2]}`
      const product = await read(`${path}/variant?${new URLSearchParams({ product_id, variant_id })}`)
      if (product?.id !== item.id || product.product_id !== product_id || product.variant_id !== variant_id) {
        throw new Error('Mismatched Shopify selection')
      }
      return {
        id: product.id, title: product.name, variant_label: product.variant_name,
        price: product.prices?.[0] && { ...product.prices[0], status: product.price_status, context: 'shopBasePrice' },
        stock: product.stock && { status: product.stock.status, quantity: product.stock.sellable_quantity, context: 'shopTotalStock' },
      }
    },
  }
  if (provider === 'yandex-market') return {
    select: async item => {
      const product = await commercePilotApi(workspaceId, marketerConnectors[provider].connector).preview(id, item)
      const { title } = readPilotProduct(product, item, false)
      const price = Array.isArray(product.prices) ? product.prices[0] : null
      return { id: item.id, title, price: price && { ...price, status: 'reported' } }
    },
  }
  if (provider === 'wildberries' && capabilities.includes('import_product_images')) {
    const pilot = commercePilotApi(workspaceId, marketerConnectors[provider].connector)
    return {
      searchLabel: 'productId.wildberries',
      search: async article => {
        if (!/^[1-9]\d{0,17}$/.test(article)) throw new Error('Invalid WB article')
        const sizes = readPilotSizes(await pilot.sizes(id, article))
        return sizes.map(({ size_id, label }) => ({
          id: `${article}:${size_id}`, title: article, variant_label: label, variant_id: size_id,
        }))
      },
      select: async item => {
        const selection = { id: item.id.split(':')[0], size: item.variant_id }
        const product = await pilot.preview(id, selection)
        const { title } = readPilotProduct(product, selection, true)
        return { ...item, title, price: { ...product.product.price, status: product.price_status } }
      },
    }
  }
  // The shared chat reads and validates opaque selectors for the selected source.
  return {
    select: async item => {
      const numericId = provider === 'wildberries' ? /^[1-9]\d{0,17}$/ : /^[1-9]\d{0,18}$/
      if (!numericId.test(item.id)) throw new Error('Invalid product selection')
      return { id: item.id, title: item.title }
    },
  }
}

export function commerceMarketerApi(workspaceId, binding) {
  const base = marketerPath(workspaceId, binding.provider, binding.id)
  return {
    history: ({ conversationId, signal } = {}) => read(
      `${base}/chat/history${conversationId ? `?${new URLSearchParams({ conversation_id: conversationId })}` : ''}`, signal),
    send: payload => post(`${base}/chat`, payload),
    apply: cardId => post(`${base}/actions/${part(cardId)}/apply`),
    result: cardId => read(`${base}/actions/${part(cardId)}/result`),
    ...selectedProduct(workspaceId, binding),
  }
}
