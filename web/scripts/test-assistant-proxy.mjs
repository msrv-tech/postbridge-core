import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { createServer } from 'vite'

test('general assistant requests reach the SaaS proxy instead of the SPA', async () => {
  const requests = []
  const upstream = http.createServer((request, response) => {
    requests.push({ path: request.url, method: request.method })
    response.writeHead(401, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ code: 'TEST_AUTH_REQUIRED' }))
  })
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
  const previousTarget = process.env.VITE_API_PROXY_TARGET
  process.env.VITE_API_PROXY_TARGET = `http://127.0.0.1:${upstream.address().port}`
  let server
  try {
    server = await createServer({
      root: fileURLToPath(new URL('..', import.meta.url)),
      logLevel: 'error',
      server: { host: '127.0.0.1', port: 0 },
    })
    await server.listen()
    const base = `http://127.0.0.1:${server.httpServer.address().port}`
    const routes = [
      { path: '/assistant/help', method: 'POST' },
      { path: '/assistant/expenses', method: 'GET' },
      { path: '/assistant/expenses/test-expense/resolve', method: 'POST' },
    ]
    for (const { path, method } of routes) {
      const response = await fetch(base + path, { method })
      assert.equal(response.status, 401)
      assert.deepEqual(await response.json(), { code: 'TEST_AUTH_REQUIRED' })
    }
    assert.deepEqual(requests, routes)
  } finally {
    await server?.close()
    await new Promise(resolve => upstream.close(resolve))
    if (previousTarget === undefined) delete process.env.VITE_API_PROXY_TARGET
    else process.env.VITE_API_PROXY_TARGET = previousTarget
  }
})
