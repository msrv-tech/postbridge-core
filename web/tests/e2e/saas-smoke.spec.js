import { expect, test } from '@playwright/test'

const saasUser = {
  id: 'user-1',
  display_name: 'SaaS User',
  email: 'user@example.test',
  is_platform_admin: false,
  current_workspace_id: 'ws-1',
  workspaces: [{ id: 'ws-1', name: 'Acme Workspace' }],
  app_mode: 'saas',
  features: {
    billing: { enabled: true },
    workspaces: { enabled: true },
    multi_tenant: { enabled: true },
    managed_credentials: { enabled: true, mode: 'bff' },
    local_auth: { enabled: false },
    agent: { enabled: true },
    media_generation: { enabled: true },
    review_queue: { enabled: true },
  },
  capabilities: {
    billing: { enabled: true },
    workspaces: { enabled: true },
    multiTenant: { enabled: true },
    managedCredentials: { enabled: true, mode: 'bff' },
    localAuth: { enabled: false },
    agent: { enabled: true },
    mediaGeneration: { enabled: true },
    reviewQueue: { enabled: true },
  },
}

const posts = [
  {
    id: 'post-1',
    title: 'Hosted smoke draft',
    content_md: 'Draft from SaaS BFF.',
    status: 'draft',
    created_at: '2026-05-15T00:00:00Z',
    updated_at: '2026-05-15T00:00:00Z',
  },
]

async function mockSaasBff(page, calls = []) {
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const json = (body, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })

    if (path.startsWith('/assets/') || path === '/postbridge-mark.svg' || path.endsWith('.png') || path.endsWith('.ico')) {
      return route.continue()
    }

    if (path === '/api/news') {
      calls.push({ path, method })
      return json({ items: [], source_url: 'https://example.com/news' })
    }
    if (path === '/auth/providers') {
      calls.push({ path, method })
      return json({ providers: [{ id: 'email', label: 'Email' }, { id: 'telegram', label: 'Telegram' }] })
    }
    if (path === '/me') {
      calls.push({ path, method, authorization: request.headers().authorization || '' })
      return json(saasUser)
    }
    if (path === '/workspaces/ws-1/posts' && method === 'GET') {
      calls.push({ path, method })
      return json({ items: posts, total: posts.length, limit: 20, offset: 0 })
    }
    if (path === '/workspaces/ws-1/posts' && method === 'POST') {
      calls.push({ path, method, body: request.postData() || '' })
      return json(
        {
          code: 'BILLING_USAGE_X_PUBLISH_CREDITS_LIMIT',
          message: 'Monthly X publishing limit exceeded.',
          details: {
            limit: 10,
            current: 10,
            requested_delta: 1,
          },
        },
        402,
      )
    }
    if (path === '/workspaces/ws-1/channel-registry') {
      calls.push({ path, method })
      return json({
        items: [
          {
            id: 'channel-postbridge',
            platform: 'postbridge',
            title: 'Postbridge source',
            platform_channel_id: 'pb/e2e',
            can_read: true,
            can_write: true,
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      })
    }
    if (path === '/workspaces/ws-1/media/generation-jobs') {
      calls.push({ path, method })
      return json({ items: [] })
    }
    if (path === '/workspaces/ws-1/dashboard/summary') {
      calls.push({ path, method })
      return json({
        billing: {
          plan_code: 'pro',
          ai_platform_adapt_enabled: true,
          subscription_status: 'active',
        },
        totals: {},
      })
    }
    if (path === '/workspaces/ws-1/settings') {
      calls.push({ path, method })
      return json({ image_style_prompt: '' })
    }
    if (path === '/workspaces/ws-1/core-publication-targets') {
      calls.push({ path, method })
      return json({ items: [], total: 0, limit: 20, offset: 0 })
    }
    if (path === '/workspaces/ws-1/posts/platform-previews') {
      calls.push({ path, method })
      return json({ items: [] })
    }
    if (path === '/workspaces/ws-1/agent/workspace-policy') {
      calls.push({ path, method })
      return json({
        editor_instructions: '',
        search_instructions: '',
        preferred_domains: [],
        blocked_domains: [],
        blocked_url_patterns: [],
      })
    }

    return route.continue()
  })
}

async function mockAdapterContractApi(page, calls = []) {
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const pathWithSearch = `${url.pathname}${url.search}`
    const path = url.pathname
    const method = request.method()

    if (
      path.startsWith('/src/') ||
      path.startsWith('/node_modules/') ||
      path.startsWith('/@') ||
      path.startsWith('/assets/') ||
      path.endsWith('.svg') ||
      path.endsWith('.png') ||
      path.endsWith('.ico')
    ) {
      return route.continue()
    }

    calls.push({
      path: pathWithSearch,
      method,
      authorization: request.headers().authorization || '',
      body: request.postData() || '',
    })

    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'ok',
        items: [],
        providers: [],
        token: 'token',
        deep_link: 'https://t.me/postbridge_bot?start=web_test',
        session_token: 'tg-session',
        url: 'https://example.test/auth',
        status: 'pending',
      }),
    })
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
  })
})

test('returns an OAuth provider login to the server-side consent endpoint', async ({ page }) => {
  const returnTo = '/oauth/authorize?response_type=code&client_id=mcp-test&state=state-1'
  await page.route(`**${returnTo}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: '<h1>OAuth consent reached</h1>',
    })
  })
  await page.addInitScript((value) => {
    window.sessionStorage.setItem('postbridge.auth_return_to', value)
  }, returnTo)

  await page.goto('/#token=oauth-session-token')

  await expect(page).toHaveURL(new RegExp(`${returnTo.replace(/[?]/g, '\\?')}$`))
  await expect(page.getByRole('heading', { name: 'OAuth consent reached' })).toBeVisible()
})

test('keeps SaaS public marketing surfaces at root', async ({ page }) => {
  const calls = []
  await mockSaasBff(page, calls)

  await page.goto('/')
  await expect(page).toHaveURL('/')
  await expect(page.getByLabel('Main navigation').getByRole('link', { name: 'Pricing' })).toBeVisible()
  await expect(page.getByLabel('Main navigation').getByRole('link', { name: 'News' })).toBeVisible()

  await page.goto('/pricing')
  await expect(page).toHaveURL('/pricing')
  await expect(page.getByRole('heading', { name: 'AI, channels, and bridges with transparent plans.' })).toBeVisible()
})

test('uses SaaS BFF contracts for an authenticated workspace', async ({ page }) => {
  const calls = []
  await mockSaasBff(page, calls)
  await page.addInitScript(() => {
    window.localStorage.setItem('postbridge_token', 'saas-token')
  })

  await page.goto('/')

  await expect(page).toHaveURL(/\/workspaces\/ws-1\/content$/)
  await expect(page.getByRole('heading', { name: 'Content' })).toBeVisible()
  await expect(page.getByText('Hosted smoke draft')).toBeVisible()

  expect(calls).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ path: '/me', method: 'GET', authorization: 'Bearer saas-token' }),
      expect.objectContaining({ path: '/workspaces/ws-1/posts', method: 'GET' }),
      expect.objectContaining({ path: '/workspaces/ws-1/media/generation-jobs', method: 'GET' }),
    ]),
  )
  expect(calls.some((call) => call.path.startsWith('/api/app/'))).toBe(false)
})

test('shows an upgrade paywall when X publishing credits are exhausted', async ({ page }) => {
  const calls = []
  await mockSaasBff(page, calls)
  await page.addInitScript(() => {
    window.localStorage.setItem('postbridge_token', 'saas-token')
    window.localStorage.setItem('postbridge.locale', 'en')
  })

  await page.goto('/workspaces/ws-1/content/new')

  await expect(page.getByRole('heading', { name: 'New post' })).toBeVisible()
  await page.getByRole('textbox', { name: 'Title' }).fill('X paywall smoke')
  const editor = page.locator('.w-md-editor textarea').first()
  await expect(editor).toBeVisible()
  await editor.fill('A short X post that should hit the monthly credit paywall.')

  await page.getByRole('button', { name: 'Publish' }).click()

  await expect(page.getByRole('heading', { name: 'X publishing limit reached' })).toBeVisible()
  await expect(page.getByText('Your plan includes 10 X credits per month.')).toBeVisible()
  const metrics = page.locator('.post-editor-x-paywall-metrics')
  await expect(metrics.getByText('0')).toBeVisible()
  await expect(metrics.getByText('remaining')).toBeVisible()
  await expect(metrics.getByText('1')).toBeVisible()
  await expect(metrics.getByText('needed now')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upgrade plan' })).toBeVisible()
  await expect(page.getByText('Monthly X publishing limit exceeded.')).toHaveCount(0)
  await expect(page).toHaveURL(/\/workspaces\/ws-1\/content\/new$/)

  expect(calls).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ path: '/workspaces/ws-1/posts', method: 'POST' }),
    ]),
  )
})

test('keeps SaaS adapter contracts on BFF paths', async ({ page }) => {
  const calls = []
  await mockAdapterContractApi(page, calls)
  await page.goto('/')
  await page.evaluate(async () => {
    window.localStorage.setItem('postbridge_token', 'saas-token')
    const [
      account,
      agent,
      auth,
      authFlows,
      billing,
      bridges,
      channels,
      content,
      dashboard,
      jobs,
      media,
      news,
      publicationTargets,
      workspaceSettings,
    ] = await Promise.all([
      import('/src/adapters/account.js'),
      import('/src/adapters/agent.js'),
      import('/src/adapters/auth.js'),
      import('/src/adapters/authFlows.js'),
      import('/src/adapters/billing.js'),
      import('/src/adapters/bridges.js'),
      import('/src/adapters/channels.js'),
      import('/src/adapters/content.js'),
      import('/src/adapters/dashboard.js'),
      import('/src/adapters/jobs.js'),
      import('/src/adapters/media.js'),
      import('/src/adapters/news.js'),
      import('/src/adapters/publicationTargets.js'),
      import('/src/adapters/workspaceSettings.js'),
    ])

    const ws = 'ws-1'
    await auth.loadCurrentUser()
    await authFlows.listAuthProviders()
    await authFlows.requestMagicLink({ email: 'user@example.test' })
    await authFlows.verifyMagicLink({ code: 'ABC123' })
    await authFlows.startTelegramWebLinkSession()
    await authFlows.fetchTelegramWebLinkStatus('tg-session')
    await account.updateCurrentUser({ timezone: 'Europe/Moscow' })

    await content.listContentItems(ws, { status: 'draft', limit: 10, offset: 5 })
    await content.getContentItem(ws, 'post-1')
    await content.createContentItem(ws, { title: 'Draft' })
    await content.updateContentItem(ws, 'post-1', { title: 'Updated' })
    await content.deleteContentItem(ws, 'post-1')

    await channels.listChannelRegistry(ws)
    await channels.getChannelRegistryItem(ws, 'channel-1')
    await channels.createChannelRegistryItem(ws, { platform: 'telegram', title: 'TG' })
    await channels.updateChannelRegistryItem(ws, 'channel-1', { title: 'TG updated' })
    await channels.deleteChannelRegistryItem(ws, 'channel-1')
    await channels.validateChannelRegistryItem(ws, { platform: 'telegram', platform_channel_id: '@postbridge' })
    await channels.requestMaxChannelVerification(ws, '-123')
    await channels.verifyMaxChannel(ws, { platform_channel_id: '-123', code: '1234' })
    await channels.createVkCommunityCredential(ws, { code: 'vk-code' })
    await channels.getLinkedinAuthorizeUrl(ws)
    await channels.listLinkedinOrganizations(ws, { code: 'li-code' })
    await channels.createLinkedinAccessTokenCredential(ws, { code: 'li-code' })
    await channels.createConnection(ws, { source_channel_id: 'src', target_channel_id: 'dst' })

    await bridges.listBridges(ws)
    await bridges.updateBridge(ws, 'bridge-1', { adaptation_mode: 'rule_only' })
    await bridges.deleteBridge(ws, 'bridge-1')

    await dashboard.getDashboardSummary(ws)
    await dashboard.listDashboardJobs(ws)
    await jobs.startHistoricalImportJob(ws, { bridge_id: 'bridge-1' })
    await jobs.getJob(ws, 'job-1')
    await jobs.runJobAction(ws, 'job-1', 'pause')
    await jobs.deleteJob(ws, 'job-1')

    await billing.listBillingPlans(ws)
    await billing.requestBillingEmail({ email: 'billing@example.test' })
    await billing.verifyBillingEmail({ code: 'BILL' })
    await billing.createSubscription(ws, { plan_code: 'pro' })
    await billing.cancelSubscription(ws, { reason: 'test' })
    await billing.initMigrationPayment(ws, { provider: 'tbank', bridgeId: 'bridge-1' })

    await media.listMediaGenerationJobs(ws, { limit: 3 })
    await media.startMediaGenerationJob(ws, { content_item_id: 'post-1', target: 'cover' })
    await media.getMediaGenerationJob(ws, 'media-job-1')

    await news.listNews({ limit: 2, offset: 1, query: 'release' })
    await news.getNewsItem('launch')
    await publicationTargets.listPublicationTargetProjections(ws, { limit: 4, offset: 2 })
    await publicationTargets.dispatchPublicationTarget(ws, 'target-1')
    await workspaceSettings.getWorkspaceSettings(ws)
    await workspaceSettings.updateWorkspaceSettings(ws, { image_style_prompt: 'Clean product image' })

    await agent.listAgentReviewQueue(ws)
    await agent.getAgentReviewItem(ws, 'review-1')
    await agent.resolveAgentReviewItem(ws, 'review-1', { action: 'approve' })
    await agent.listAgentRuns(ws)
    await agent.getAgentRun(ws, 'run-1')
    await agent.listAgentRunSteps(ws, 'run-1')
    await agent.createAgentRun(ws, { mode: 'topic_scout' })
    await agent.getAgentEditorTimeline(ws, 'post-1')
    await agent.createAgentEditorMessage(ws, 'post-1', { content: 'Improve this' })
    await agent.getPostPlatformPreviews(ws, { content_item_id: 'post-1' })
    await agent.getAgentCandidate(ws, 'candidate-1')
    await agent.listAgentTasks(ws)
    await agent.createAgentTask(ws, { mode: 'topic_scout' })
    await agent.pauseAgentTask(ws, 'task-1')
    await agent.resumeAgentTask(ws, 'task-1')
    await agent.deleteAgentTask(ws, 'task-1')
    await agent.runAgentTask(ws, 'task-1')
    await agent.getAgentAnalyticsOverview(ws)
    await agent.getAgentAnalyticsTimeseries(ws, { days: 7 })
    await agent.getAgentAnalyticsQuality(ws, { days: 7 })
    await agent.listAgentPolicies(ws)
    await agent.upsertAgentPolicy(ws, { mode: 'topic_scout' })
    await agent.getWorkspaceAgentPolicy(ws)
    await agent.upsertWorkspaceAgentPolicy(ws, { editor_instructions: 'Be concise' })
    await agent.getAgentEmbeddingsLifecycle(ws, { channelId: 'channel-1', channelLimit: 5, channelOffset: 1 })
    await agent.reindexAgentChannelEmbeddings(ws, 'channel-1', { reason: 'test' })
    await agent.rotateAgentChannelEmbeddings(ws, 'channel-1', { reason: 'test' })
    await agent.reindexAgentEmbeddingDrift(ws, { reason: 'test' })
    await agent.maintainAgentEmbeddings(ws, { limit: 10 })
    await agent.compactAgentEmbeddings(ws, { limit: 10 })
    await agent.cleanupAgentRuntime(ws, { limit: 10 })
  })

  expect(calls.some((call) => call.path.startsWith('/api/app/'))).toBe(false)
  const expectedCalls = [
    { method: 'GET', path: '/me', authorization: 'Bearer saas-token' },
    { method: 'GET', path: '/auth/providers' },
    { method: 'POST', path: '/auth/magic-link/request' },
    { method: 'PATCH', path: '/me' },
    { method: 'GET', path: '/workspaces/ws-1/posts?status=draft&limit=10&offset=5' },
    { method: 'POST', path: '/workspaces/ws-1/channel-registry' },
    { method: 'POST', path: '/workspaces/ws-1/connections/create' },
    { method: 'GET', path: '/workspaces/ws-1/bridges' },
    { method: 'PATCH', path: '/workspaces/ws-1/bridges/bridge-1' },
    { method: 'DELETE', path: '/workspaces/ws-1/bridges/bridge-1' },
    { method: 'POST', path: '/workspaces/ws-1/jobs/start' },
    { method: 'POST', path: '/workspaces/ws-1/billing/subscription/create' },
    { method: 'GET', path: '/workspaces/ws-1/media/generation-jobs?limit=3' },
    { method: 'GET', path: '/api/news?limit=2&offset=1&q=release' },
    { method: 'POST', path: '/workspaces/ws-1/core-publication-targets/target-1/dispatch' },
    { method: 'PUT', path: '/workspaces/ws-1/settings' },
    { method: 'POST', path: '/workspaces/ws-1/agent/runs' },
    { method: 'POST', path: '/workspaces/ws-1/posts/platform-previews' },
    { method: 'POST', path: '/workspaces/ws-1/agent/tasks/task-1/run' },
    { method: 'PUT', path: '/workspaces/ws-1/agent/workspace-policy' },
    { method: 'POST', path: '/workspaces/ws-1/agent/embeddings/maintenance' },
    { method: 'POST', path: '/workspaces/ws-1/agent/cleanup' },
  ]
  const missingCalls = expectedCalls.filter((expected) =>
    !calls.some((call) =>
      call.method === expected.method &&
      call.path === expected.path &&
      (expected.authorization == null || call.authorization === expected.authorization)
    )
  )
  expect(missingCalls).toEqual([])
})
