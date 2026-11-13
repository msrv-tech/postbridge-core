import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

// Snapshots generated from the canonical backend projector, never product fixtures.
const ru = JSON.parse(readFileSync(new URL('./fixtures/connectors-ru.json', import.meta.url)))
const io = JSON.parse(readFileSync(new URL('./fixtures/connectors-io.json', import.meta.url)))
const user = { id: 'connector-review-user', display_name: 'Review user', is_platform_admin: false,
  workspaces: [{ id: 'ws-one', name: 'Studio', role: 'owner' }, { id: 'ws-two', name: 'Shop', role: 'owner' }],
  features: { billing: { enabled: false } }, app_mode: 'saas' }
const observedApiCalls = new WeakMap()

test.afterEach(async ({ page }, testInfo) => {
  const calls = observedApiCalls.get(page) || []
  await testInfo.attach('catalog-data-api-calls', { body: Buffer.from(JSON.stringify(calls)), contentType: 'application/json' })
  expect(calls.filter(call => /^\/workspaces\/[^/]+\/connectors(?:\/[^/]+)?$/.test(call.path))).toEqual([])
})

async function mockApi(page, { market = 'ru', locale = market === 'ru' ? 'ru' : 'en', authenticated = false, catalog, personal, delay, workspaceStatus = 200, workspaceHtml = false } = {}) {
  const calls = []
  observedApiCalls.set(page, calls)
  await page.addInitScript(({ locale, authenticated }) => {
    localStorage.setItem('postbridge.locale', locale)
    if (authenticated) localStorage.setItem('postbridge_token', 'synthetic-review-session')
  }, { locale, authenticated })
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    if (request.isNavigationRequest()) return route.continue()
    if (!/^\/(?:api\/|workspaces\/|auth\/|me$|assistant\/)/.test(path)) return route.continue()
    calls.push({ method: request.method(), path })
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    // Human navigation above still serves the SPA. Data fetches must never use it.
    if (/^\/workspaces\/[^/]+\/connectors(?:\/[^/]+)?$/.test(path)) return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Wrong data API namespace</title>' })
    if (path === '/api/app/runtime-config') return json({ app_mode: 'saas', i18n: { default_locale: locale } })
    if (path === '/api/news') return json({ items: [] })
    if (path === '/me') return json(user)
    if (path === '/auth/providers') return json({ providers: [{ id: 'email', type: 'magic_code' }] })
    if (path === '/auth/magic-link/request') return json({ message: 'Check the test inbox' })
    if (path === '/auth/magic-link/verify') return json({ token: 'synthetic-review-session' })
    if (path === '/api/connectors') return json(catalog || (market === 'ru' ? ru : io))
    const workspace = path.match(/^\/api\/workspaces\/([^/]+)\/connectors$/)?.[1]
    if (workspace) {
      if (delay) await delay(workspace)
      if (workspaceStatus !== 200) return json({ code: 'WORKSPACE_ACCESS_DENIED', message: 'Workspace access denied' }, workspaceStatus)
      if (workspaceHtml) return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Unexpected upstream HTML</title>' })
      const source = catalog || (market === 'ru' ? ru : io)
      return json({ ...source, workspace_id: workspace, items: source.items.map(item => ({ ...item,
        detail_url: `/workspaces/${workspace}/connectors/${item.id}`,
        personal: personal?.(workspace, item) || { status: 'unknown', scope: item.authorization_scope },
      })) })
    }
    const manifestId = path.match(/^\/api\/external-apps\/([^/]+)\/manifest$/)?.[1]
    if (manifestId) {
      const item = ru.items.find(item => item.id === manifestId) || io.items[0]
      return json({ name: item.display_name, connector: item,
        documentation_url: 'https://example.test/provider-guide',
        ...(item.kind === 'mcp' ? { mcp: { docs_url: '/mcp/docs', server_url: '/mcp' } } : {}),
      })
    }
    if (path.endsWith('/media/generation-jobs')) return json({ items: [] })
    if (path.endsWith('/videos/capabilities')) return json({ enabled: false })
    if (path.endsWith('/posts')) return json({ items: [], total: 0 })
    return json({})
  })
  return calls
}

function expectWorkspaceCatalogApi(calls, workspaceId) {
  expect(calls).toContainEqual({ method: 'GET', path: `/api/workspaces/${workspaceId}/connectors` })
  expect(calls.filter(call => /^\/workspaces\/[^/]+\/connectors(?:\/[^/]+)?$/.test(call.path))).toEqual([])
}

async function verifyLogos(page, count) {
  await expect(page.locator('[data-connector-id]')).toHaveCount(count)
  const images = page.locator('.connector-card .connector-logo img')
  await expect(images).toHaveCount(count)
  await expect.poll(() => images.evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0))).toBe(true)
  expect(await images.evaluateAll(images => images.every(image => image.alt === '' && getComputedStyle(image).objectFit === 'contain'))).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await expect(page.locator('main')).not.toContainText(/connectors\.[a-z]+\./)
}

for (const market of ['ru', 'io']) for (const theme of ['dark', 'light']) for (const width of [320, 390, 768, 1280]) {
  test(`${market} ${theme} ${width}: sourced logos and textual readiness on home/catalog`, async ({ page }) => {
    const calls = await mockApi(page, { market })
    const errors = []; page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(theme => localStorage.setItem('postbridge.theme', theme), theme)
    await page.goto('/home')
    await verifyLogos(page, market === 'ru' ? 4 : 1)
    const placement = await page.evaluate(() => {
      const formats = document.getElementById('formats'), connectors = document.getElementById('connectors')
      return Boolean(formats.compareDocumentPosition(connectors) & Node.DOCUMENT_POSITION_FOLLOWING)
    })
    expect(placement).toBe(true)
    await page.locator('#connectors .connector-catalog-link').click()
    await verifyLogos(page, market === 'ru' ? 9 : 1)
    const planned = page.locator('[data-readiness="planned"]')
    await expect(planned).toHaveCount(market === 'ru' ? 5 : 0)
    for (const card of await planned.all()) await expect(card.getByRole('link')).not.toHaveAccessibleName(/подключить|войти|connect|sign in/i)
    await expect(page.locator('[data-connector-id="chatgpt-mcp"] .connector-protocol')).toContainText('MCP')
    expect(await page.locator('[data-connector-id="chatgpt-mcp"] .connector-protocol img').getAttribute('src')).not.toEqual(await page.locator('[data-connector-id="chatgpt-mcp"] .connector-logo img').getAttribute('src'))
    expect(calls.filter(call => call.method !== 'GET')).toEqual([])
    expect(errors).toEqual([])
  })
}

test('keyboard navigation and planned guides never start installation', async ({ page }) => {
  const calls = await mockApi(page)
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto('/connectors')
  const menu = page.locator('.header-menu-toggle')
  await expect(menu).toHaveAccessibleName('Открыть меню')
  await menu.focus(); await page.keyboard.press('Enter'); await expect(menu).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('Escape'); await expect(menu).toHaveAttribute('aria-expanded', 'false'); await expect(menu).toBeFocused()
  await page.locator('[data-connector-id="ozon-app"] a').focus(); await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/connectors\/ozon-app$/)
  await expect(page.locator('main a[href^="/login"]')).toHaveCount(0)
  await expect(page.locator('.connector-detail .connector-actions')).toHaveCount(0)
  expect(calls.filter(call => call.method !== 'GET')).toEqual([])
})

test('typed sign-in resumes the guide, then an authorized workspace is selected explicitly', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto('/connectors/bitrix24-mcp')
  await page.getByRole('link', { name: 'Войти и выбрать пространство' }).click()
  await expect(page).toHaveURL(/\/login\?connector=bitrix24-mcp$/)
  await page.getByLabel('Email', { exact: true }).fill('review@example.test')
  await page.getByRole('button', { name: 'Получить код', exact: true }).click()
  await page.getByLabel('Код из письма').fill('1234')
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page).toHaveURL(/\/connectors\/bitrix24-mcp\?setup=1$/)
  await page.getByLabel('Рабочее пространство', { exact: true }).selectOption('ws-two')
  await page.getByRole('link', { name: 'Продолжить в этом пространстве' }).click()
  await expect(page).toHaveURL(/\/workspaces\/ws-two\/connectors\/bitrix24-mcp$/)
  await expect(page.getByRole('link', { name: 'Открыть инструкцию подключения' })).toHaveAttribute('href', '/mcp/docs')
  expectWorkspaceCatalogApi(calls, 'ws-two')
  expect(calls.filter(call => call.method !== 'GET').map(call => call.path)).toEqual(['/auth/magic-link/request', '/auth/magic-link/verify'])
})

test('workspace entry, scope, switching and reauth remain separate from product readiness', async ({ page }) => {
  const calls = await mockApi(page, { authenticated: true,
    personal: (workspace, item) => workspace === 'ws-one' && item.id === 'moysklad-app' ? { status: 'needs_login', scope: 'workspace', account_name: 'Studio source' } : { status: 'unknown', scope: item.authorization_scope },
  })
  await page.goto('/')
  await expect(page).toHaveURL(/\/workspaces\/ws-one\/content$/)
  await page.getByRole('link', { name: 'Открыть коннекторы', exact: false }).click()
  await verifyLogos(page, 9)
  const source = page.locator('[data-connector-id="moysklad-app"]')
  await expect(source).toHaveAttribute('data-readiness', 'available')
  await expect(source.locator('.connector-personal')).toContainText('Нужен повторный вход')
  await expect(page.locator('[data-connector-id="bitrix24-mcp"] .connector-personal')).toHaveAttribute('data-connection-scope', 'user_client')
  await page.locator('.workspace-switcher-trigger').click()
  await page.getByRole('button', { name: /Shop/ }).click()
  await expect(page).toHaveURL(/\/workspaces\/ws-two\/connectors$/)
  await expect(source.locator('.connector-personal')).toContainText('Состояние подключения не проверено')
  await expect(page.locator('main')).not.toContainText('Studio source')
  expectWorkspaceCatalogApi(calls, 'ws-one')
  expectWorkspaceCatalogApi(calls, 'ws-two')
  expect(calls.filter(call => call.method !== 'GET')).toEqual([])
})

for (const locale of ['ru', 'en']) for (const width of [390, 1280]) test(`disabled rollout labels ${locale} ${width}: unavailable differs from planned and available`, async ({ page }) => {
  const catalog = structuredClone(ru)
  Object.assign(catalog.items.find(item => item.id === 'moysklad-app'), { public_readiness: 'planned', rollout: 'disabled', setup_mode: 'none', next_action: { kind: 'details', method: 'GET' } })
  const text = locale === 'ru' ? { unavailable: 'Недоступно', planned: 'Планируется', available: 'Доступно', group: 'Пока недоступны', plannedGroup: 'Планируются', future: 'направление развития' }
    : { unavailable: 'Unavailable', planned: 'Planned', available: 'Available', group: 'Currently unavailable', plannedGroup: 'Planned', future: 'planned direction' }
  const calls = await mockApi(page, { locale, authenticated: true, catalog })
  await page.setViewportSize({ width, height: 1000 })
  await page.addInitScript(theme => localStorage.setItem('postbridge.theme', theme), width === 390 ? 'light' : 'dark')
  await page.goto('/connectors')
  await verifyLogos(page, 9)
  const source = page.locator('[data-connector-id="moysklad-app"]')
  await expect(source).toHaveAttribute('data-readiness', 'planned')
  await expect(source).toHaveAttribute('data-rollout', 'disabled')
  await expect(source.locator('.connector-readiness')).toContainText(text.unavailable)
  await expect(source.locator('.connector-readiness')).not.toContainText(text.planned)
  await expect(page.locator('[data-connector-id="yandex-market-source"] .connector-readiness')).toContainText(text.planned)
  await expect(page.locator('[data-connector-id="bitrix24-mcp"] .connector-readiness')).toContainText(text.available)
  await expect(page.locator('#connector-planned-title')).toHaveText(text.group)
  if (process.env.POSTBRIDGE_UI_ARTIFACT_DIR) await page.locator('.public-shell').screenshot({ path: `${process.env.POSTBRIDGE_UI_ARTIFACT_DIR}/disabled-${locale}-${width}.png` })
  await source.getByRole('link').click()
  await expect(page.locator('.connector-detail')).toHaveAttribute('data-rollout', 'disabled')
  await expect(page.locator('.connector-detail')).not.toContainText(text.future)
  await expect(page.locator('.connector-detail .connector-actions')).toHaveCount(0)
  await page.goto('/workspaces/ws-one/connectors')
  await expect(source.locator('.connector-readiness')).toContainText(text.unavailable)
  await expect(source.locator('.connector-personal')).toHaveCount(0)
  await source.getByRole('link').click()
  await expect(page.locator('.connector-detail .connector-actions')).toHaveCount(0)
  expectWorkspaceCatalogApi(calls, 'ws-one')
  for (const item of catalog.items) if (item.rollout === 'disabled') item.rollout = 'not_launched'
  await page.goto('/connectors')
  await expect(page.locator('#connector-planned-title')).toHaveText(text.plannedGroup)
  await expect(source.locator('.connector-readiness')).toContainText(text.planned)
  expect(calls.filter(call => call.method !== 'GET')).toEqual([])
})

test('guest scoped URL opens a public guide, foreign workspace never issues a scoped request', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto('/workspaces/not-a-member/connectors/chatgpt-mcp')
  await expect(page).toHaveURL(/\/connectors\/chatgpt-mcp$/)
  expect(calls.some(call => /^\/(?:api\/)?workspaces\/not-a-member\//.test(call.path))).toBe(false)
})

test('authenticated foreign workspace never issues a catalog data request', async ({ page }) => {
  const calls = await mockApi(page, { authenticated: true })
  await page.goto('/workspaces/not-a-member/connectors')
  await expect(page).toHaveURL(/\/workspaces\/ws-one\/content$/)
  expect(calls.some(call => /^\/(?:api\/)?workspaces\/not-a-member\//.test(call.path))).toBe(false)
})

test('denied catalog API fails closed in the authorized workspace', async ({ page }) => {
  const calls = await mockApi(page, { authenticated: true, workspaceStatus: 403 })
  await page.goto('/workspaces/ws-one/connectors')
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить каталог')
  await expect(page.locator('[data-connector-id]')).toHaveCount(0)
  expectWorkspaceCatalogApi(calls, 'ws-one')
  expect(calls.filter(call => call.method !== 'GET')).toEqual([])
})

for (const format of ['legacy-json', 'html']) test(`malformed authorized catalog ${format} never offers setup`, async ({ page }) => {
  const calls = await mockApi(page, { authenticated: true, workspaceHtml: format === 'html',
    catalog: { deployment: 'ru', items: [{ id: 'old-app', implemented: true, status: 'available' }] },
  })
  await page.goto('/workspaces/ws-one/connectors')
  if (format === 'legacy-json') await expect(page.getByRole('alert')).toContainText('Не удалось загрузить каталог')
  else await expect(page.getByText('Для этой установки пока нет доступных коннекторов.')).toBeVisible()
  await expect(page.locator('[data-connector-id]')).toHaveCount(0)
  expectWorkspaceCatalogApi(calls, 'ws-one')
  expect(calls.filter(call => call.method !== 'GET')).toEqual([])
})

test('opening ordinary login clears a stale connector continuation', async ({ page }) => {
  await mockApi(page)
  await page.addInitScript(() => sessionStorage.setItem('postbridge.connector_intent', JSON.stringify({ id: 'bitrix24-mcp', startedAt: Date.now() })))
  await page.goto('/login')
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('postbridge.connector_intent'))).toBeNull()
})

test('an older backend cannot silently turn technical status into readiness', async ({ page }) => {
  await mockApi(page, { catalog: { deployment: 'ru', items: [{ id: 'old-app', implemented: true, status: 'available' }] } })
  await page.goto('/connectors')
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить каталог')
  await expect(page.locator('[data-connector-id]')).toHaveCount(0)
})

for (const theme of ['dark', 'light']) for (const width of [320, 390, 768, 1280]) {
  test(`authorized ${theme} ${width}: compact header and catalog fit`, async ({ page }) => {
    await mockApi(page, { authenticated: true })
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(theme => localStorage.setItem('postbridge.theme', theme), theme)
    await page.goto('/workspaces/ws-one/connectors')
    await verifyLogos(page, 9)
    if (width <= 768) {
      await expect(page.locator('.header-menu-toggle')).toBeVisible()
      await expect(page.locator('.header-menu-toggle')).toHaveAttribute('aria-expanded', 'false')
    }
    if (process.env.POSTBRIDGE_UI_ARTIFACT_DIR) {
      // Close the existing assistant through its own UI for an unobstructed review capture.
      const minimize = page.getByRole('button', { name: 'Свернуть помощника' })
      if (await minimize.count()) await minimize.click()
      await page.screenshot({ path: `${process.env.POSTBRIDGE_UI_ARTIFACT_DIR}/authorized-${width}-${theme}.png`, fullPage: true })
    }
  })
}

test('late data from a previous workspace cannot overwrite the current catalog', async ({ page }) => {
  let release
  const pending = new Promise(resolve => { release = resolve })
  const calls = await mockApi(page, { authenticated: true, delay: workspace => workspace === 'ws-one' ? pending : undefined,
    personal: (workspace, item) => workspace === 'ws-one' ? { status: 'connected', scope: item.authorization_scope, account_name: 'Previous workspace source' } : { status: 'unknown', scope: item.authorization_scope },
  })
  const oldRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/workspaces/ws-one/connectors')
  await page.goto('/workspaces/ws-one/connectors'); await oldRequest
  await page.locator('.workspace-switcher-trigger').click(); await page.getByRole('button', { name: /Shop/ }).click()
  await verifyLogos(page, 9)
  const oldResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/workspaces/ws-one/connectors')
  release(); await oldResponse
  await expect(page).toHaveURL(/\/workspaces\/ws-two\/connectors$/)
  await expect(page.locator('main')).not.toContainText('Previous workspace source')
  expectWorkspaceCatalogApi(calls, 'ws-one')
  expectWorkspaceCatalogApi(calls, 'ws-two')
  expect(calls.filter(call => call.method !== 'GET')).toEqual([])
})

test('OAuth return_to takes precedence over a connector continuation', async ({ page }) => {
  await mockApi(page)
  await page.goto('/login?connector=chatgpt-mcp&return_to=' + encodeURIComponent('/oauth/authorize?client_id=approved'))
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('postbridge.auth_return_to'))).toBe('/oauth/authorize?client_id=approved')
  expect(await page.evaluate(() => sessionStorage.getItem('postbridge.connector_intent'))).toBeNull()
})

test('runtime recognizes the self-host path without switching the SaaS dev server mode', async ({ page }) => {
  await mockApi(page); await page.goto('/connectors')
  expect(await page.evaluate(async () => {
    const runtime = await import('/src/adapters/runtime.js')
    const path = location.pathname
    const hosted = runtime.isSelfhostMode()
    history.replaceState(null, '', '/web/connectors')
    const local = runtime.isSelfhostMode()
    history.replaceState(null, '', path)
    return { hosted, local }
  })).toEqual({ hosted: false, local: true })
})
