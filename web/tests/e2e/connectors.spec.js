import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

// Snapshots generated from the canonical backend projector, never product fixtures.
const ru = JSON.parse(readFileSync(new URL('./fixtures/connectors-ru.json', import.meta.url)))
const io = JSON.parse(readFileSync(new URL('./fixtures/connectors-io.json', import.meta.url)))
const user = { id: 'connector-review-user', display_name: 'Review user', is_platform_admin: false,
  workspaces: [{ id: 'ws-one', name: 'Studio', role: 'owner' }, { id: 'ws-two', name: 'Shop', role: 'owner' }],
  features: { billing: { enabled: false } }, app_mode: 'saas' }
const observedApiCalls = new WeakMap()
const homeAssetProbes = new WeakMap()
const homeRouteTasks = new WeakMap()

test.afterEach(async ({ page }, testInfo) => {
  const pending = homeRouteTasks.get(page)
  while (pending?.size) await Promise.all([...pending])
  await page.unrouteAll({ behavior: 'wait' })
  const probe = homeAssetProbes.get(page)
  if (probe) expect(probe.fulfilled).toBe(true)
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
  await expect.poll(() => images.evaluateAll((images, count) => images.length === count && images.every(image => image.alt === '' && getComputedStyle(image).objectFit === 'contain'), count)).toBe(true)
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

// Synthetic product descriptors follow the reviewed public-catalog wire. Expected
// regional sets below are independent of the frontend projection implementation.
const homeProviders = [
  ['telegram', 'Telegram', 'direct_publication'], ['facebook', 'Facebook', 'direct_publication'],
  ['instagram', 'Instagram', 'direct_publication'], ['youtube', 'YouTube', 'video_publication'],
  ['tiktok', 'TikTok', 'video_publication'], ['x', 'X', 'direct_publication'],
  ['linkedin', 'LinkedIn', 'direct_publication'], ['bluesky', 'Bluesky', 'direct_publication'],
  ['mastodon', 'Mastodon', 'direct_publication'], ['max', 'MAX', 'direct_publication'],
  ['vk', 'VK', 'direct_publication'], ['zen', 'Dzen', 'rss_import_or_feed'],
  ['rss', 'RSS', 'rss_import_or_feed'], ['postbridge', 'Postbridge', 'internal_source'],
]
const homeExpectedIds = {
  ru: ['telegram', 'youtube', 'tiktok', 'linkedin', 'mastodon', 'max', 'vk', 'zen', 'rss', 'postbridge'],
  io: ['telegram', 'facebook', 'instagram', 'youtube', 'tiktok', 'x', 'linkedin', 'bluesky', 'mastodon', 'vk', 'zen', 'rss', 'postbridge'],
}
function homeCatalog(market) {
  return { items: homeProviders.filter(([id]) => homeExpectedIds[market].includes(id)).map(([id, display_name, presentation_role]) => ({
    id, display_name, presentation_role, status: 'available', detail_path: market === 'io' ? `/platforms/${id}` : null,
  })) }
}


async function mockHomeCatalog(page, { market = 'io', locale = 'en', authenticated = false, catalog = homeCatalog(market), providerStatus = 200, providerBody, waitForCatalog, assetDelayMs = 0 } = {}) {
  const calls = await mockApi(page, { market, locale, authenticated })
  const baseURL = test.info().project.use.baseURL
  const assetProbe = { requested: false, fulfilled: false }
  if (assetDelayMs) homeAssetProbes.set(page, assetProbe)
  const pending = new Set()
  homeRouteTasks.set(page, pending)
  // Keep the actual market hostname in the browser while serving only local
  // test assets; no DNS or production/provider requests are involved.
  await page.route(/^http:\/\/postbridge\.(?:ru|io)\//, async route => {
    const task = (async () => {
      const url = new URL(route.request().url())
      if (/^\/(?:api\/|workspaces\/|auth\/|me$|assistant\/)/.test(url.pathname) && !route.request().isNavigationRequest()) return route.fallback()
      const delayed = assetDelayMs && url.pathname === '/connector-brands/openai.svg'
      if (delayed) {
        assetProbe.requested = true
        await new Promise(resolve => setTimeout(resolve, assetDelayMs))
      }
      const response = await route.fetch({ url: `${baseURL}${url.pathname}${url.search}` })
      await route.fulfill({ response })
      if (delayed) assetProbe.fulfilled = true
    })()
    pending.add(task)
    try {
      return await task
    } finally {
      pending.delete(task)
    }
  })
  const providerRequests = []
  await page.route('**/api/providers', async route => {
    providerRequests.push({ method: route.request().method(), headers: await route.request().allHeaders() })
    if (waitForCatalog) await waitForCatalog
    return route.fulfill({ status: providerStatus, contentType: providerBody === undefined ? 'application/json' : 'text/html', body: providerBody ?? JSON.stringify(catalog) })
  })
  return { calls, providerRequests, assetProbe }
}

test('Home canonical catalog drains a pending local asset handler before page teardown', async ({ page }) => {
  const { assetProbe } = await mockHomeCatalog(page, { assetDelayMs: 1000 })
  const requested = page.waitForRequest('**/connector-brands/openai.svg')
  await page.goto('http://postbridge.io/home', { waitUntil: 'domcontentloaded' })
  await requested
  await expect.poll(() => assetProbe.requested).toBe(true)
  expect(assetProbe.fulfilled).toBe(false)
  // The shared afterEach must finish the real pending proxy handler while the
  // page/request context is still alive, then assert successful fulfillment.
})

async function visibleHomeIds(page) {
  return page.locator('#platforms [data-provider-id]').evaluateAll(cards => cards.map(card => card.dataset.providerId))
}

for (const [market, locale, width] of [['ru', 'ru', 320], ['ru', 'en', 1280], ['io', 'en', 390], ['io', 'ru', 1280]]) {
  test(`Home canonical catalog ${market} ${locale} ${width}: complete roles, links and visible last row`, async ({ page }, testInfo) => {
    const { providerRequests } = await mockHomeCatalog(page, { market, locale })
    const errors = []; page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`http://postbridge.${market}/home#platforms`)
    await expect.poll(() => visibleHomeIds(page)).toEqual(homeExpectedIds[market])
    const section = page.locator('#platforms')
    const labels = locale === 'ru' ? ['Публикации', 'Видеопубликации', 'RSS и импорт', 'Внутренний источник'] : ['Publishing', 'Video publishing', 'RSS and import', 'Internal source']
    for (const [id, label] of [['telegram', labels[0]], ['youtube', labels[1]], ['tiktok', labels[1]], ['zen', labels[2]], ['rss', labels[2]], ['postbridge', labels[3]]]) {
      await expect(section.locator(`[data-provider-id="${id}"] .platform-showcase-role`)).toHaveText(label)
    }
    await expect(section.locator('[data-provider-id="zen"] h3')).toHaveText('Dzen')
    const links = section.getByRole('link')
    await expect(links).toHaveCount(market === 'io' ? 13 : 0)
    for (const id of market === 'io' ? homeExpectedIds.io : []) {
      await expect(section.locator(`[data-provider-id="${id}"] a`)).toHaveAttribute('href', `/platforms/${id}`)
    }
    if (market === 'io') {
      await links.first().focus(); await expect(links.first()).toBeFocused()
      await page.keyboard.press('Tab'); await expect(links.nth(1)).toBeFocused()
    }
    const last = section.locator('[data-provider-id="postbridge"]')
    await last.scrollIntoViewIfNeeded(); await expect(last).toBeInViewport()
    expect(await section.locator('[data-provider-id]').evaluateAll(cards => cards.every(card => {
      const r = card.getBoundingClientRect()
      return r.left >= 0 && r.right <= innerWidth && card.scrollWidth <= card.clientWidth
    }))).toBe(true)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await expect.poll(() => section.locator('.platform-logo img').evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0 && image.alt === ''))).toBe(true)
    expect(providerRequests.every(request => request.method === 'GET' && !request.headers.authorization && !request.headers.cookie)).toBe(true)
    expect(errors).toEqual([])
    if (process.env.POSTBRIDGE_HOME_SCREENSHOTS) {
      const path = `${process.env.POSTBRIDGE_HOME_SCREENSHOTS}/home-${market}-${locale}-${width}.png`
      await section.screenshot({ path }); await testInfo.attach('home-platform-catalog', { path, contentType: 'image/png' })
    }
  })
}

for (const authenticated of [false, true]) test(`Home canonical catalog receiver independence ${authenticated ? 'member' : 'guest'} and locale invariance`, async ({ page }) => {
  const { calls } = await mockHomeCatalog(page, { authenticated })
  await page.goto('http://postbridge.io/home')
  await expect.poll(() => visibleHomeIds(page)).toEqual(homeExpectedIds.io)
  // No receiver, credential, or bridge request participates in membership.
  expect(calls.filter(call => /\/(?:channels|bridges|rules)(?:\/|$)/.test(call.path))).toEqual([])
  const language = page.locator('.public-header .language-select select')
  await language.selectOption('ru')
  await expect(page.locator('#platforms [data-provider-id="postbridge"] .platform-showcase-role')).toHaveText('Внутренний источник')
  expect(await visibleHomeIds(page)).toEqual(homeExpectedIds.io)
})

test('Home canonical catalog loading never exposes the legacy shortlist', async ({ page }) => {
  let release
  const waiting = new Promise(resolve => { release = resolve })
  await mockHomeCatalog(page, { waitForCatalog: waiting })
  await page.goto('http://postbridge.io/home')
  await expect(page.locator('#platforms [role="status"]')).toContainText('Loading')
  await expect(page.locator('#platforms .platform-showcase-item')).toHaveCount(0)
  release()
  await expect.poll(() => visibleHomeIds(page)).toEqual(homeExpectedIds.io)
})

for (const [name, failure] of [
  ['HTTP failure', { providerStatus: 503 }],
  ['HTML instead of JSON', { providerBody: '<!doctype html><title>Unavailable</title>' }],
  ['missing catalog', { catalog: {} }],
  ['malformed descriptor', { catalog: { items: [{ id: 'telegram' }] } }],
]) test(`Home canonical catalog ${name} has no legacy fallback`, async ({ page }) => {
  await mockHomeCatalog(page, { ...failure })
  await page.goto('http://postbridge.io/home')
  await expect(page.locator('#platforms [role="status"]')).toHaveText('The platform list is temporarily unavailable.')
  await expect(page.locator('#platforms .platform-showcase-item')).toHaveCount(0)
})

test('Home canonical catalog old wire stays complete with neutral labels and no guessed links', async ({ page }) => {
  const catalog = homeCatalog('io')
  catalog.items.forEach(item => { delete item.presentation_role; delete item.detail_path })
  await mockHomeCatalog(page, { catalog })
  await page.goto('http://postbridge.io/home')
  await expect.poll(() => visibleHomeIds(page)).toEqual(homeExpectedIds.io)
  await expect(page.locator('#platforms a')).toHaveCount(0)
  await expect(page.locator('#platforms .platform-showcase-role').first()).toHaveText('Supported platform')
})

test('Home canonical catalog normalizes aliases, keeps new IDs and rejects unsafe detail paths', async ({ page }) => {
  const catalog = { items: [
    { id: ' twitter ', display_name: 'X', presentation_role: 'direct_publication', detail_path: '/platforms/x' },
    { id: 'x', display_name: 'Duplicate X', presentation_role: 'direct_publication' },
    { id: 'future-provider', display_name: 'Future provider', presentation_role: '__proto__', detail_path: 'https://example.test/platforms/future-provider' },
    { id: 'off-provider', display_name: 'Disabled provider', status: 'disabled' },
    { id: 'private-provider', display_name: 'Private route', detail_path: '/workspaces/private' },
  ] }
  await mockHomeCatalog(page, { catalog })
  await page.goto('http://postbridge.io/home')
  await expect.poll(() => visibleHomeIds(page)).toEqual(['x', 'future-provider', 'private-provider'])
  await expect(page.locator('#platforms a')).toHaveCount(1)
  await expect(page.locator('#platforms [data-provider-id="future-provider"] .platform-showcase-role')).toHaveText('Supported platform')
  await expect(page.locator('#platforms [data-provider-id="future-provider"] .local-brand-fallback')).toHaveCount(1)
})

test('Home canonical catalog information link performs native document navigation', async ({ page }) => {
  await mockHomeCatalog(page)
  let documentNavigation = false
  await page.route('http://postbridge.io/platforms/youtube', route => {
    documentNavigation = route.request().isNavigationRequest()
    return route.fulfill({ contentType: 'text/html', body: '<h1>Local SSR detail fixture</h1>' })
  })
  await page.goto('http://postbridge.io/home')
  await page.locator('#platforms [data-provider-id="youtube"] a').click()
  await expect(page.getByRole('heading', { name: 'Local SSR detail fixture' })).toBeVisible()
  expect(documentNavigation).toBe(true)
})

test('Home canonical catalog public 401 keeps the member session and sends no credentials', async ({ page, context }) => {
  await context.addCookies([{ name: 'private_session', value: 'synthetic-private-cookie', url: 'http://postbridge.io' }])
  const { providerRequests } = await mockHomeCatalog(page, { authenticated: true, providerStatus: 401 })
  await page.goto('http://postbridge.io/home')
  await expect(page.locator('#platforms [role="status"]')).toHaveText('The platform list is temporarily unavailable.')
  expect(await page.evaluate(() => localStorage.getItem('postbridge_token'))).toBe('synthetic-review-session')
  expect(providerRequests.length).toBeGreaterThan(0)
  expect(providerRequests.every(request => !request.headers.authorization && !request.headers.cookie)).toBe(true)
})

if (process.env.POSTBRIDGE_HOME_MODE) test('Home canonical catalog explicit selfhost keeps the existing showcase without provider requests', async ({ page }) => {
  const { providerRequests } = await mockHomeCatalog(page)
  await page.goto(process.env.POSTBRIDGE_HOME_MODE === 'flag-at-root' ? '/home' : '/web/home')
  await expect(page.locator('#platforms .platform-showcase-item h3')).toHaveText(['Telegram', 'LinkedIn', 'X', 'Bluesky', 'Mastodon', 'RSS'])
  await expect(page.locator('#platforms [data-provider-id]')).toHaveCount(0)
  await expect(page.locator('#platforms [role="status"]')).toHaveCount(0)
  await expect(page.locator('#platforms .platform-showcase-role')).toHaveCount(0)
  expect(providerRequests).toEqual([])
})

test('Home canonical catalog build-disabled policy intersects a stale full API catalog', async ({ page }) => {
  const market = process.env.POSTBRIDGE_HOME_PROFILE || 'io'
  const catalog = { items: homeProviders.map(([id, display_name, presentation_role]) => ({ id, display_name, presentation_role, detail_path: market === 'io' ? `/platforms/${id}` : null })) }
  await mockHomeCatalog(page, { market, catalog })
  await page.goto(`http://postbridge.${market}/home`)
  const disabled = new Set(String(process.env.VITE_POSTBRIDGE_DISABLED_PLATFORMS || '').split(',').map(id => id.trim().toLowerCase().replace(/^twitter$/, 'x')))
  await expect.poll(() => visibleHomeIds(page)).toEqual(homeProviders.map(([id]) => id).filter(id => !disabled.has(id)))
})

const pilotWorkspaceApi = '/api/workspaces/ws-one/connectors'
const pilotCatalogPath = '/workspaces/ws-one/connectors'
const wbPilot = 'wildberries-source'
const marketPilot = 'yandex-market-source'
const pilotFixture = (id, connectionIds = [], canConnect = false) => ({ id, can_connect: canConnect, connection_ids: connectionIds })

async function showPilotCatalog(page, privatePilots) {
  await mockApi(page, { authenticated: true, locale: 'en' })
  await page.route('**/api/workspaces/*/connectors', route => {
    const workspace = new URL(route.request().url()).pathname.split('/')[3]
    return route.fulfill({ json: { ...ru, workspace_id: workspace,
      private_pilots: workspace === 'ws-one' ? privatePilots() : [],
    } })
  })
}

function draftResult(id) {
  return { route: `/workspaces/ws-one/content/${id}`, post_reference: { id },
    content_result: { status: 'draft', persisted: true } }
}

async function mockPilotEditor(page, post) {
  await page.route(`**/workspaces/ws-one/posts/${post.id}`, async route => {
    if (route.request().method() === 'PATCH') Object.assign(post, route.request().postDataJSON())
    return route.fulfill({ json: post })
  })
}

test('private pilot WB picks an exact size, reviews verified price, retries a single draft and returns from the editor', async ({ page }) => {
  const connectionId = 'wb-test-33'
  await showPilotCatalog(page, () => [pilotFixture(wbPilot, [connectionId])])
  await page.setViewportSize({ width: 390, height: 844 })
  const base = '/workspaces/ws-one/wildberries/connections/' + connectionId
  const result = draftResult('wb-draft-test')
  const requests = { preview: 0, prepare: [], apply: [], writes: 0 }
  let previewLimit = true
  let pending = true
  const draft = { id: 'wb-draft-test', title: 'Canvas shoes', content_md: '', status: 'draft' }
  await mockPilotEditor(page, draft)
  await page.route('**/wildberries/connections/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === base) return route.fulfill({ json: { id: connectionId, name: 'Shoe shop', status: 'connected', version: 3 } })
    if (path === base + '/products/531/sizes') return route.fulfill({ json: { items: [{ size_id: 12, label: 'S' }, { size_id: 781, label: 'M' }] } })
    if (path === base + '/products/531') {
      expect(url.searchParams.get('size_id')).toBe('781')
      requests.preview += 1
      if (previewLimit) {
        previewLimit = false
        return route.fulfill({ status: 429, json: { code: 'WB_RATE_LIMITED' } })
      }
      return route.fulfill({ json: { size_id: 781, price_status: 'reported', product: {
        product_id: '531', title: 'Canvas shoes', description: 'Inspected material', price: { amount: '0', currency: 'RUB' },
      } } })
    }
    if (path === base + '/products/531/draft-review') {
      requests.prepare.push(request.postDataJSON())
      return route.fulfill({ json: {
        card: { id: '261', status: 'proposed', summary: { text: 'Size 781 · 13.75 RUB' } },
        source: { article: '531', size_id: 781, price_status: 'reported', price: { amount: '13.75', currency: 'RUB' } },
      } })
    }
    if (path === base + '/draft-reviews/261/apply') {
      requests.apply.push(path)
      if (pending) {
        pending = false
        requests.writes += 1
        draft.content_md = 'Size 781 · 13.75 RUB'
        return route.abort('failed')
      }
      return route.fulfill({ json: result })
    }
    return route.fulfill({ status: 500, json: { code: 'UNEXPECTED_TEST_ROUTE' } })
  })

  await page.goto(pilotCatalogPath)
  await page.getByRole('region', { name: 'Private pilots' }).getByRole('link', { name: 'Wildberries', exact: true }).click()
  await page.getByLabel('WB article').fill('531')
  await page.getByRole('button', { name: 'Find sizes' }).click()
  await page.getByLabel('WB size').selectOption('781')
  await page.getByRole('button', { name: 'Preview product' }).click()
  await expect(page.getByRole('alert')).toContainText('rate limit')
  await page.getByRole('button', { name: 'Preview product' }).click()
  await expect(page.getByText('Price: 0 RUB', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Prepare draft' }).click()
  await expect(page.getByText('Price: 13.75 RUB', { exact: true })).toBeVisible()
  await expect(page.getByText('Size 781 · 13.75 RUB', { exact: true })).toBeVisible()
  expect(requests.writes).toBe(0)
  await page.getByRole('button', { name: 'Confirm draft creation' }).click()
  await expect(page.getByRole('alert')).toContainText('Try again')
  await page.reload()
  await page.getByRole('button', { name: 'Retry confirmation' }).click()
  await expect(page).toHaveURL(result.route)
  const editor = page.locator('.w-md-editor textarea').first()
  await expect(editor).toHaveValue('Size 781 · 13.75 RUB')
  await page.getByRole('button', { name: 'Hide assistant' }).click()
  await editor.fill('Reviewed copy for size 781')
  await page.getByRole('button', { name: 'Save draft', exact: true }).click()
  await expect.poll(() => draft.content_md).toBe('Reviewed copy for size 781')
  await page.goto(pilotCatalogPath + '/' + wbPilot)
  await expect(page.getByRole('link', { name: 'Open draft' })).toHaveAttribute('href', result.route)
  await page.getByRole('link', { name: 'Open draft' }).click()
  await expect(editor).toHaveValue('Reviewed copy for size 781')
  await page.goBack()
  await expect(page.getByRole('link', { name: 'Open draft' })).toBeVisible()
  expect(requests.prepare).toEqual([{ size_id: 781 }])
  expect(requests.apply).toEqual([base + '/draft-reviews/261/apply', base + '/draft-reviews/261/apply'])
  expect(requests.preview).toBe(2)
  expect(requests.writes).toBe(1)
})

test('private pilot Market connects, reads an exact offer, safely retries a draft and revokes access', async ({ page }) => {
  const connection = { id: 'market-test-66', account_name: 'Toy store', status: 'connected', campaign_id: '77529' }
  let connected = false
  const requests = []
  const result = draftResult('market-draft-test')
  const base = pilotWorkspaceApi + '/yandex-market/connections'
  const offerId = 'toy & blue/92'
  let saved = false
  await showPilotCatalog(page, () => [pilotFixture(marketPilot, connected ? [connection.id] : [], true)])
  await mockPilotEditor(page, { id: 'market-draft-test', title: 'Toy car', content_md: 'Supplier draft', status: 'draft' })
  await page.route('**/yandex-market/connections**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === base && request.method() === 'POST') {
      expect(request.postDataJSON()).toEqual({ campaign_id: '77529', api_key: 'synthetic-market-key' })
      connected = true
      return route.fulfill({ status: 201, json: connection })
    }
    if (path === base + '/' + connection.id && request.method() === 'DELETE') {
      connection.status = 'revoked'
      return route.fulfill({ json: { status: 'revoked' } })
    }
    if (path === base + '/' + connection.id) return route.fulfill({ json: connection })
    if (path.endsWith('/offer')) {
      expect(url.searchParams.get('offer_id')).toBe(offerId)
      return route.fulfill({ json: { id: offerId, name: 'Toy car', description: 'Blue metal body', prices: [{ amount: '112.65', currency: 'RUB' }] } })
    }
    if (path.endsWith('/drafts')) {
      requests.push(request.postDataJSON())
      if (!saved) {
        saved = true
        return route.fulfill({ json: { ...result, route: '/workspaces/ws-two/content/foreign' } })
      }
      return route.fulfill({ json: { ...result, idempotent_replay: true } })
    }
    return route.fulfill({ status: 500, json: { code: 'UNEXPECTED_TEST_ROUTE' } })
  })
  await page.goto(pilotCatalogPath + '/' + marketPilot)
  await page.getByLabel('Campaign ID').fill('77529')
  await page.getByLabel('Read-only API key').fill('synthetic-market-key')
  await page.getByRole('button', { name: 'Connect store' }).click()
  await page.getByLabel('Seller offer ID', { exact: true }).fill(offerId)
  await page.getByRole('button', { name: 'Preview product' }).click()
  await expect(page.getByText('Price: 112.65 RUB')).toBeVisible()
  expect(requests).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm draft creation' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page).toHaveURL(pilotCatalogPath + '/' + marketPilot)
  await page.reload()
  await page.getByRole('button', { name: 'Retry confirmation' }).click()
  await expect(page).toHaveURL(result.route)
  await page.goBack()
  await expect(page.getByRole('link', { name: 'Open draft' })).toHaveAttribute('href', result.route)
  await page.getByRole('button', { name: 'Disconnect store' }).click()
  await expect(page.getByText('Disconnect access to this store?')).toBeVisible()
  await page.getByRole('button', { name: 'Disconnect store' }).click()
  await expect(page.getByText('Toy store · Disconnected')).toBeVisible()
  await expect(page.getByLabel('Seller offer ID', { exact: true })).toHaveCount(0)
  expect(requests).toHaveLength(2)
  expect(requests[0]).toEqual({ offer_id: offerId, idempotency_key: expect.any(String) })
  expect(requests[1]).toEqual(requests[0])
  expect(await page.evaluate(() => JSON.stringify(sessionStorage))).not.toContain('synthetic-market-key')
})

test('private pilot is hidden without explicit access and refused status hides supplier actions', async ({ page }) => {
  let allowed = false
  await showPilotCatalog(page, () => allowed ? [pilotFixture(wbPilot, ['synthetic-denied'], true)] : [])
  const reads = []
  await page.route('**/wildberries/connections/**', route => {
    reads.push(route.request().method())
    return route.fulfill({ status: 403, json: { code: 'WB_ACCESS_DENIED' } })
  })
  await page.goto(pilotCatalogPath + '/' + wbPilot)
  await expect(page.locator('[data-connector-detail]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Private pilots' })).toHaveCount(0)
  expect(reads).toEqual([])
  allowed = true
  await page.goto('/connectors/' + wbPilot)
  await expect(page.locator('[data-connector-detail]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Private pilots' })).toHaveCount(0)
  await page.goto('/workspaces/ws-two/connectors/' + wbPilot)
  await expect(page.locator('[data-connector-detail]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Private pilots' })).toHaveCount(0)
  expect(reads).toEqual([])
  await page.goto(pilotCatalogPath + '/' + wbPilot)
  await expect(page.getByRole('alert')).toContainText('connection is unavailable')
  await expect(page.getByLabel('WB article')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Connect store' })).toHaveCount(0)
  expect(reads).toEqual(['GET'])
})
