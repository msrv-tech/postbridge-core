import { expect, test } from '@playwright/test'

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

async function mockSelfhostApi(page) {
  const posts = []
  const channels = []
  const bridges = []
  await page.route('**/api/app/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()
    const json = (body) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })

    if (path === '/api/app/runtime-config') return json(runtimeConfig)
    if (path === '/api/app/session') return json(session)
    if (path === '/api/app/bootstrap') return json(session)
    if (path === '/api/app/content-items' && method === 'POST') {
      const payload = route.request().postDataJSON()
      const post = {
        id: `post-${posts.length + 1}`,
        content_md: payload.content_md || '',
        title: payload.title || '',
        summary: payload.summary || '',
        status: payload.status || 'draft',
        created_at: '2026-05-14T00:00:00Z',
        updated_at: '2026-05-14T00:00:00Z',
      }
      posts.unshift(post)
      return json(post)
    }
    if (path === '/api/app/content-items') return json({ items: posts, total: posts.length, limit: 20, offset: 0 })
    if (path === '/api/app/channels' && method === 'POST') {
      const payload = route.request().postDataJSON()
      const channel = {
        id: `channel-${channels.length + 1}`,
        platform: payload.platform,
        platform_channel_id: payload.platform_channel_id,
        title: payload.title || payload.platform_channel_id,
        display: payload.title || payload.platform_channel_id,
        can_read: Boolean(payload.can_read),
        can_write: Boolean(payload.can_write),
        live_sync_source_supported: payload.platform === 'postbridge',
      }
      channels.push(channel)
      return json(channel)
    }
    if (path === '/api/app/channels') return json({ items: channels })
    if (path === '/api/app/connections/create' && method === 'POST') {
      const payload = route.request().postDataJSON()
      const source = channels.find((channel) => channel.platform_channel_id === payload.source_channel_id)
      const target = channels.find((channel) => channel.platform_channel_id === payload.target_channel_id)
      const bridge = {
        id: `bridge-${bridges.length + 1}`,
        source_channel_id: source?.id || 'channel-1',
        target_channel_id: target?.id || `target-${bridges.length + 1}`,
        source_platform: payload.source_platform,
        target_platform: payload.target_platform,
        source_platform_channel_id: payload.source_channel_id,
        target_platform_channel_id: payload.target_channel_id,
        source_display: payload.source_display,
        target_display: payload.target_display,
        mode: payload.mode || 'live_sync',
        status: 'active',
        live_sync_source_supported: true,
      }
      bridges.push(bridge)
      return json(bridge)
    }
    if (path === '/api/app/bridges') return json({ items: bridges })
    if (path === '/api/app/dashboard/jobs') return json({ items: [] })
    if (path === '/api/app/dashboard/summary') {
      return json({
        tenant_id: session.tenant.id,
        billing: { plan_code: 'selfhost', ai_platform_adapt_enabled: true },
        channels_count: channels.length,
        bridges_count: bridges.length,
        totals: {},
      })
    }
    if (path === '/api/app/media/generation-jobs') return json({ items: [] })

    return json({ items: [] })
  })
}

test.beforeEach(async ({ page }) => {
  await mockSelfhostApi(page)
})

test('bootstraps into the self-host workspace', async ({ page }) => {
  await page.goto('/web/')

  await expect(page).toHaveURL(/\/web\/workspaces\/local\/content/)
  await expect(page.getByRole('heading', { name: 'Content' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Workspace navigation' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Content' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Channels' })).toBeVisible()
})

test('navigates core workflow screens without SaaS billing surfaces', async ({ page }) => {
  await page.goto('/web/')

  await page.getByRole('link', { name: 'Channels' }).click()
  await expect(page).toHaveURL(/\/web\/workspaces\/local\/channels/)
  await expect(page.getByRole('heading', { level: 1, name: 'Channels' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Pricing' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'News' })).toHaveCount(0)

  await page.goto('/web/pricing')
  await expect(page).toHaveURL(/\/web\/workspaces\/local\/content/)

  await page.goto('/web/news')
  await expect(page).toHaveURL(/\/web\/workspaces\/local\/content/)
})

test('creates a draft from the self-host content editor', async ({ page }) => {
  await page.goto('/web/')

  await page.getByRole('banner').getByRole('link', { name: 'Create post' }).click()
  await expect(page).toHaveURL(/\/web\/workspaces\/local\/content\/new/)
  await expect(page.getByRole('heading', { name: 'New post' })).toBeVisible()

  await page.getByRole('textbox', { name: 'Title' }).fill('Self-host smoke draft')
  await page.locator('.w-md-editor textarea').first().fill('Smoke test body from Playwright.')
  await page.getByRole('button', { name: 'Save draft' }).click()

  await expect(page).toHaveURL(/\/web\/workspaces\/local\/content$/)
  await expect(page.getByText('Self-host smoke draft')).toBeVisible()
  await expect(page.getByText('Draft', { exact: true })).toBeVisible()
})

test('creates a channel and bridge in the self-host workflow', async ({ page }) => {
  await page.goto('/web/')

  await page.getByRole('link', { name: 'Channels' }).click()
  await page.getByRole('link', { name: 'Add channel' }).first().click()
  await expect(page).toHaveURL(/\/web\/workspaces\/local\/channels\/add/)

  await page.locator('#platform').selectOption('postbridge')
  await page.getByRole('button', { name: 'Check' }).click()
  await page.getByRole('button', { name: 'Add channel' }).click()

  await expect(page).toHaveURL(/\/web\/workspaces\/local\/channels\?success=channel_added/)
  await expect(page.getByText('Channel added. Add more channels and create a bridge between them.')).toBeVisible()
  await expect(page.locator('.channel-link-badge-name', { hasText: 'Postbridge' })).toBeVisible()

  await page.getByRole('link', { name: 'Add channel' }).first().click()
  await expect(page).toHaveURL(/\/web\/workspaces\/local\/channels\/add/)
  await page.locator('#platform').selectOption('rss')
  await page.locator('#rss-mode').selectOption('target')
  await page.locator('#channel-id').fill('rss')
  await page.locator('#title').fill('RSS')
  await page.getByRole('button', { name: 'Check' }).click()
  await page.getByRole('button', { name: 'Add channel' }).click()

  await expect(page).toHaveURL(/\/web\/workspaces\/local\/migrate\?success=channel_added/)

  await page.locator('#source-channel-id').selectOption('channel-1')
  await page.locator('#target-channel-id').selectOption('channel-2')
  await page.getByRole('button', { name: 'Create bridge' }).click()

  await expect(page).toHaveURL(/\/web\/workspaces\/local\/channels\?success=channel_connected/)
  await expect(page.getByText('Bridge connected. Live sync is active.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Bridges' })).toBeVisible()
  await expect(page.locator('.channel-link-badge-name', { hasText: 'RSS' }).first()).toBeVisible()
})
