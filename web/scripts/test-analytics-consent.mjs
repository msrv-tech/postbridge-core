import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

function browser({ stored = null, storageBlocked = false } = {}) {
  const scripts = []
  const calls = []
  let reloads = 0
  const storage = new Map(stored === null ? [] : [['postbridge.analytics-consent', stored]])
  const document = {
    head: { appendChild: (script) => scripts.push(script) },
    createElement: () => ({ remove() { scripts.splice(scripts.indexOf(this), 1) } }),
    getElementById: (id) => scripts.find((script) => script.id === id),
  }
  const window = {
    localStorage: {
      getItem(key) { if (storageBlocked) throw new Error('blocked'); return storage.get(key) ?? null },
      setItem(key, value) { if (storageBlocked) throw new Error('blocked'); storage.set(key, value) },
    },
    location: { pathname: '/', reload() { reloads += 1 } },
    ym: (...args) => calls.push(args),
  }
  const context = vm.createContext({ window, document })
  const consent = readFileSync(new URL('../src/cookieConsent.js', import.meta.url), 'utf8')
  const metrika = readFileSync(new URL('../src/metrika.js', import.meta.url), 'utf8')
    .replace(/^import .*\n/gm, '')
    .replace('import.meta.env.VITE_YANDEX_METRIKA_COUNTER_ID', '123456')
  vm.runInContext(`${consent}\n${metrika}`.replace(/^export /gm, '') + `
    globalThis.api = { readCookieConsent, writeCookieConsent, initMetrika, hitMetrika, reachMetrikaGoal, enableMetrika, disableMetrika }
  `, context)
  return { api: context.api, window, scripts, calls, storage, get reloads() { return reloads } }
}

test('initial visits, rejected choices and invalid records never load or queue analytics', () => {
  for (const stored of [null, 'broken', '{}', '{"version":0,"value":"accepted"}', '{"version":1,"value":"rejected"}']) {
    const app = browser({ stored })
    app.api.initMetrika()
    app.api.hitMetrika('/private')
    app.api.reachMetrikaGoal('auth_completed', { method: 'test' })
    assert.equal(app.scripts.length, 0)
    assert.equal(app.calls.length, 0)
  }
})

test('explicit acceptance starts a single counter and is remembered on later visits', () => {
  const app = browser()
  assert.equal(app.api.writeCookieConsent('accepted'), true)
  app.api.enableMetrika()
  app.api.hitMetrika('/pricing')
  assert.equal(app.scripts.length, 1)
  assert.equal(app.calls.filter((call) => call[1] === 'init').length, 1)
  assert.equal(app.calls[0][2].webvisor, true)
  const nextVisit = browser({ stored: app.storage.get('postbridge.analytics-consent') })
  nextVisit.api.initMetrika()
  assert.equal(nextVisit.scripts.length, 1)
})

test('withdrawal stops the counter, removes its script and blocks later SPA events', () => {
  const app = browser({ stored: '{"version":1,"value":"accepted"}' })
  app.api.initMetrika()
  app.api.writeCookieConsent('rejected')
  app.api.disableMetrika()
  const count = app.calls.length
  app.api.hitMetrika('/dashboard')
  app.api.reachMetrikaGoal('payment')
  assert.equal(app.calls.length, count)
  assert.equal(app.calls.at(-1)[1], 'destruct')
  assert.equal(app.window.disableYaCounter123456, true)
  assert.equal(app.scripts.length, 0)
  assert.equal(app.reloads, 1)
  const nextVisit = browser({ stored: app.storage.get('postbridge.analytics-consent') })
  nextVisit.api.initMetrika()
  assert.equal(nextVisit.scripts.length, 0)
})

test('storage failure does not authorize analytics', () => {
  const app = browser({ storageBlocked: true })
  assert.equal(app.api.writeCookieConsent('accepted'), false)
  app.api.enableMetrika()
  assert.equal(app.scripts.length, 0)
  assert.equal(app.calls.length, 0)
})

test('withdrawal can block this session even if an old accepted record cannot be overwritten', () => {
  const app = browser({ stored: '{"version":1,"value":"accepted"}' })
  app.api.initMetrika()
  app.api.disableMetrika({ reload: false })
  const count = app.calls.length
  app.api.hitMetrika('/private')
  assert.equal(app.calls.length, count)
  assert.equal(app.reloads, 0)
})
