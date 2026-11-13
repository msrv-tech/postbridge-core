import assert from 'node:assert/strict'
import { beforeEach, test } from 'node:test'
import { readFile } from 'node:fs/promises'
import { normalizeConnectorId, connectorResumePath, rememberConnectorIntent, consumeConnectorIntent } from '../src/adapters/connectorIntent.js'
import { projectConnectorCatalog, connectorAllowsSetup, connectorPersonalState, safeConnectorHref } from '../src/adapters/connectorPolicy.js'
import { normalizeAuthReturnTo } from '../src/adapters/authReturnTo.js'

const storage = new Map()
globalThis.window = { location: { origin: 'https://postbridge.test' }, sessionStorage: {
  getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key),
} }
beforeEach(() => storage.clear())

test('real catalog adapter uses the API namespace, encodes workspace ids and preserves the public URL', async () => {
  const calls = []
  globalThis.__connectorNamespaceTransport = async (path, options) => {
    calls.push({ path, method: options?.method || 'GET' })
    return { items: [] }
  }
  try {
    // Replace only the transport import; execute the real adapter function bodies.
    const source = await readFile(new URL('../src/adapters/externalApps.js', import.meta.url), 'utf8')
    const transport = 'data:text/javascript,' + encodeURIComponent('export const api = (...args) => globalThis.__connectorNamespaceTransport(...args)')
    assert.ok(source.includes("'./apiClient'"))
    const module = await import('data:text/javascript;base64,' + Buffer.from(source.replace("'./apiClient'", JSON.stringify(transport))).toString('base64'))
    await module.listConnectors()
    await module.listConnectors('ws-one')
    await module.listConnectors('ws/odd name?')
    assert.deepEqual(calls, [
      { path: '/api/connectors', method: 'GET' },
      { path: '/api/workspaces/ws-one/connectors', method: 'GET' },
      { path: '/api/workspaces/ws%2Fodd%20name%3F/connectors', method: 'GET' },
    ])
  } finally { delete globalThis.__connectorNamespaceTransport }
})

test('typed connector continuation cannot carry an arbitrary URL or weaken OAuth return_to', () => {
  for (const value of ['//evil.test', 'https://evil.test', '../oauth', 'moysklad-app?return_to=//evil.test', 'a/b', 'a\\b', 'a%2fb', 'a#token=x', '', null]) {
    assert.equal(normalizeConnectorId(value), null)
    assert.equal(connectorResumePath(value), null)
  }
  assert.equal(connectorResumePath('chatgpt-mcp'), '/connectors/chatgpt-mcp?setup=1')
  assert.equal(normalizeAuthReturnTo('/connectors/chatgpt-mcp'), null)
  assert.equal(normalizeAuthReturnTo('/oauth/authorize?client_id=approved'), '/oauth/authorize?client_id=approved')
})
test('connector intent is consumed once, expires and rejects future or malformed state', () => {
  rememberConnectorIntent('bitrix24-mcp')
  assert.equal(consumeConnectorIntent(), '/connectors/bitrix24-mcp?setup=1')
  assert.equal(consumeConnectorIntent(), null)
  for (const value of [JSON.stringify({ id: 'bitrix24-mcp', startedAt: Date.now() - 600001 }), JSON.stringify({ id: 'bitrix24-mcp', startedAt: Date.now() + 60000 }), '{', JSON.stringify({ id: '//evil.test', startedAt: Date.now() })]) {
    storage.set('postbridge.connector_intent', value)
    assert.equal(consumeConnectorIntent(), null)
    assert.equal(storage.has('postbridge.connector_intent'), false)
  }
  rememberConnectorIntent('bitrix24-mcp'); rememberConnectorIntent('https://evil.test')
  assert.equal(consumeConnectorIntent(), null)
})
test('canonical UI projection is required; technical implementation does not confer readiness', () => {
  const legacy = { id: 'source', status: 'available', implemented: true }
  assert.deepEqual(projectConnectorCatalog({ deployment: 'ru', items: [legacy] }), [])
  const canonical = { ...legacy, kind: 'data', public_readiness: 'planned', sort_order: 1 }
  assert.deepEqual(projectConnectorCatalog({ deployment: 'all', items: [canonical] }), [])
  assert.deepEqual(projectConnectorCatalog({ deployment: 'ru', items: [canonical] }), [canonical])
  assert.equal(connectorAllowsSetup(canonical), false)
})
test('closed/disabled/planned and non-GET actions never enable setup', () => {
  const enabled = { public_readiness: 'available', rollout: 'public', setup_mode: 'guide', next_action: { kind: 'setup_guide', method: 'GET' } }
  assert.equal(connectorAllowsSetup(enabled), true)
  for (const override of [{ public_readiness: 'planned' }, { rollout: 'disabled' }, { rollout: 'not_launched' }, { rollout: 'closed_pilot' }, { setup_mode: 'none' }, { next_action: { kind: 'install', method: 'POST' } }]) assert.equal(connectorAllowsSetup({ ...enabled, ...override }), false)
})
test('personal state cannot cross workspace-source and user/client scopes or leak an unknown account name', () => {
  assert.deepEqual(connectorPersonalState({ kind: 'mcp', personal: { scope: 'workspace', status: 'connected', account_name: 'Other workspace' } }), { scope: 'user_client', state: 'unknown' })
  assert.deepEqual(connectorPersonalState({ kind: 'data', personal: { scope: 'workspace', status: 'unknown', account_name: 'Unverified' } }), { scope: 'workspace', state: 'unknown', accountName: '' })
  assert.equal(connectorPersonalState({ kind: 'data', personal: { scope: 'workspace', status: 'needs_login' } }).state, 'needs_login')
})
test('instruction links reject script protocols, credentials and scheme-relative URLs', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,a', '//evil.test', '/\\evil.test', 'https://user:pass@evil.test/', '/safe\nunsafe']) assert.equal(safeConnectorHref(value, { external: true }), null)
  assert.equal(safeConnectorHref('https://other.test/setup'), null)
  assert.equal(safeConnectorHref('https://other.test/setup', { external: true }), 'https://other.test/setup')
  assert.equal(safeConnectorHref('/mcp/docs'), '/mcp/docs')
})
