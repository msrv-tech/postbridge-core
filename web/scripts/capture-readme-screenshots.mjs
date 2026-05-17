import { chromium } from '@playwright/test'
import { spawn } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..', '..')
const webRoot = path.join(root, 'web')
const outDir = path.join(root, 'docs', 'assets', 'screenshots')
const port = Number(process.env.POSTBRIDGE_SCREENSHOT_PORT || 4185)
const baseURL = `http://127.0.0.1:${port}`

const runtimeConfig = {
  app_mode: 'selfhost',
  api: { base_path: '/api/app' },
  i18n: { default_locale: 'en', locale_locked: true },
  features: {
    billing: { enabled: false },
    workspaces: { enabled: false },
    multi_tenant: { enabled: false },
    managed_credentials: { enabled: true, mode: 'core' },
    local_auth: { enabled: true },
    agent: { enabled: true },
    media_generation: { enabled: true },
    review_queue: { enabled: true },
  },
  capabilities: {
    billing: { enabled: false },
    workspaces: { enabled: false },
    multiTenant: { enabled: false },
    managedCredentials: { enabled: true, mode: 'core' },
    localAuth: { enabled: true },
    agent: { enabled: true },
    mediaGeneration: { enabled: true },
    reviewQueue: { enabled: true },
  },
}

const session = {
  authenticated: true,
  bootstrapped: true,
  app_mode: 'selfhost',
  tenant: { id: '00000000-0000-4000-8000-000000000001', name: 'Postbridge Self-host' },
  user: { id: 'local-admin', display_name: 'Local Admin', role: 'admin' },
}

const posts = [
  {
    id: 'post-1',
    title: 'Weekly product update',
    summary: 'A concise launch note ready for channel adaptation.',
    content_md: '# Weekly product update\n\nA concise launch note ready for channel adaptation.',
    content_plain: 'A concise launch note ready for channel adaptation.',
    status: 'published',
    created_at: '2026-05-17T08:30:00Z',
    updated_at: '2026-05-17T08:45:00Z',
  },
  {
    id: 'post-2',
    title: 'Migration checklist',
    summary: 'Draft instructions for moving an archive into Postbridge.',
    content_md: '# Migration checklist\n\nDraft instructions for moving an archive into Postbridge.',
    content_plain: 'Draft instructions for moving an archive into Postbridge.',
    status: 'draft',
    created_at: '2026-05-16T12:00:00Z',
    updated_at: '2026-05-16T12:20:00Z',
  },
]

const channels = [
  {
    id: 'channel-postbridge',
    platform: 'postbridge',
    kind: 'source',
    platform_channel_id: 'postbridge',
    external_id: 'postbridge',
    title: 'Postbridge Source',
    display: 'Postbridge Source',
    can_read: true,
    can_write: false,
    status: 'connected',
    live_sync_source_supported: true,
  },
  {
    id: 'channel-telegram',
    platform: 'telegram',
    kind: 'target',
    platform_channel_id: '@demo_channel',
    external_id: '@demo_channel',
    title: 'Telegram Demo',
    display: 'Telegram Demo',
    can_read: false,
    can_write: true,
    status: 'connected',
    live_sync_source_supported: false,
  },
  {
    id: 'channel-rss',
    platform: 'rss',
    kind: 'target',
    platform_channel_id: 'demo-feed',
    external_id: 'demo-feed',
    title: 'RSS Feed',
    display: 'RSS Feed',
    can_read: false,
    can_write: true,
    status: 'connected',
    live_sync_source_supported: false,
  },
]

const bridges = [
  {
    id: 'bridge-1',
    source_channel_id: 'channel-postbridge',
    target_channel_id: 'channel-telegram',
    source_platform: 'postbridge',
    target_platform: 'telegram',
    source_platform_channel_id: 'postbridge',
    target_platform_channel_id: '@demo_channel',
    source_display: 'Postbridge Source',
    target_display: 'Telegram Demo',
    status: 'active',
    mode: 'live_sync',
    settings: { adaptation_mode: 'rule_only' },
    live_sync_source_supported: true,
  },
  {
    id: 'bridge-2',
    source_channel_id: 'channel-postbridge',
    target_channel_id: 'channel-rss',
    source_platform: 'postbridge',
    target_platform: 'rss',
    source_platform_channel_id: 'postbridge',
    target_platform_channel_id: 'demo-feed',
    source_display: 'Postbridge Source',
    target_display: 'RSS Feed',
    status: 'active',
    mode: 'migration',
    settings: { adaptation_mode: 'ai_review' },
    live_sync_source_supported: true,
  },
]

function json(route, body) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function mockSelfhostApi(page) {
  await page.route('**/api/app/**', async (route) => {
    const url = new URL(route.request().url())
    const pathName = url.pathname

    if (pathName === '/api/app/runtime-config') return json(route, runtimeConfig)
    if (pathName === '/api/app/session') return json(route, session)
    if (pathName === '/api/app/bootstrap') return json(route, session)
    if (pathName === '/api/app/content-items') {
      return json(route, { items: posts, total: posts.length, limit: 20, offset: 0 })
    }
    if (pathName.startsWith('/api/app/content-items/')) {
      return json(route, posts.find((post) => pathName.includes(post.id)) || posts[0])
    }
    if (pathName === '/api/app/channels') return json(route, { items: channels })
    if (pathName === '/api/app/bridges') return json(route, { items: bridges })
    if (pathName === '/api/app/dashboard/jobs') return json(route, { items: [] })
    if (pathName === '/api/app/media/generation-jobs') return json(route, { items: [] })
    if (pathName === '/api/app/dashboard/summary') {
      return json(route, {
        tenant_id: session.tenant.id,
        billing: { plan_code: 'selfhost', ai_platform_adapt_enabled: true },
        channels_count: channels.length,
        bridges_count: bridges.length,
        totals: {},
      })
    }

    return json(route, { items: [] })
  })
}

async function waitForServer() {
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    try {
      const response = await fetch(baseURL)
      if (response.ok) return
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
  }
  throw new Error(`Timed out waiting for ${baseURL}`)
}

async function capture(page, route, fileName, heading) {
  await page.goto(`${baseURL}${route}`, { waitUntil: 'domcontentloaded' })
  if (heading) {
    await page.getByRole('heading', { name: heading }).first().waitFor({ state: 'visible' })
  }
  await page.screenshot({
    path: path.join(outDir, fileName),
    fullPage: false,
  })
}

const server = spawn(
  path.join(webRoot, 'node_modules', '.bin', 'vite'),
  ['--host', '127.0.0.1', '--port', String(port)],
  {
    cwd: webRoot,
    env: {
      ...process.env,
      VITE_BASE_PATH: '/web/',
      VITE_POSTBRIDGE_APP_MODE: 'selfhost',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
)

try {
  await mkdir(outDir, { recursive: true })
  await waitForServer()

  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  await mockSelfhostApi(page)

  await capture(page, '/web/workspaces/local/content', 'postbridge-content.png', 'Content')
  await capture(page, '/web/workspaces/local/channels', 'postbridge-channels.png', 'Channels')

  await browser.close()
} finally {
  server.kill('SIGTERM')
}

process.exit(0)
