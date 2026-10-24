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
      return json({
        providers: [
          { id: 'yandex', label: 'Yandex', type: 'oauth', start_url: '/auth/yandex/start' },
          { id: 'email', label: 'Email', type: 'magic_code' },
          { id: 'telegram', label: 'Telegram', type: 'deep_link' },
        ],
      })
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

    // Unspecified background API reads must not reach the running dev backend,
    // whose real 401 response would clear this test's synthetic login.
    if (['fetch', 'xhr'].includes(request.resourceType()) && (path.startsWith('/workspaces/') || path.startsWith('/api/app/'))) {
      return json({ code: 'TEST_ENDPOINT_NOT_MOCKED', message: 'No fixture for this endpoint.' }, 404)
    }

    return route.continue()
  })
}

test('video studio keeps creation simple and sends selected materials to the BFF', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const projectId = '00000000-0000-4000-8000-000000000071'
  const assetId = '00000000-0000-4000-8000-000000000072'
  let created
  let project
  let assistantContext
  await page.route('**/workspaces/ws-1/assistant/help', async (route) => {
    assistantContext = route.request().postDataJSON()
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      conversation_id: 'video-conversation', message: 'Your video is queued.', next_action: 'open_video',
      route: `/workspaces/ws-1/content/videos/${projectId}`,
    }) })
  })
  await page.route('**/workspaces/ws-1/videos**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path.endsWith('/capabilities')) return reply({ enabled: true, max_upload_bytes: 67108864, max_scenes: 8 })
    if (path.endsWith('/media/upload')) return reply({ media_asset_id: assetId, url: '/sample.png', kind: 'image' }, 201)
    if (route.request().method() === 'POST' && path.endsWith('/videos')) {
      created = route.request().postDataJSON()
      const current = { id: 'revision-1', version: 1, status: 'queued', stage: 'queued', scenes: created.scenes }
      project = { id: projectId, brief: created.brief, current_version: 1, current, revisions: [current] }
      return reply(project, 201)
    }
    if (path.endsWith(`/${projectId}`)) return reply(project)
    return reply({ items: [], has_more: false })
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('postbridge_token', 'saas-token')
    window.localStorage.setItem('postbridge.locale', 'en')
  })
  await page.goto('/workspaces/ws-1/content/videos')
  await page.getByRole('link', { name: 'Create video', exact: true }).click()
  await page.getByLabel('What is your video about?').fill('Show our product')
  await expect(page.getByRole('button', { name: 'Save idea', exact: true })).toBeVisible()
  await page.locator('input[type=file]').setInputFiles({ name: 'screen.png', mimeType: 'image/png', buffer: Buffer.from('test image') })
  await expect(page.getByText('screen.png', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Assemble video', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/videos/${projectId}$`))
  await expect(page.getByRole('heading', { name: 'Show our product', exact: true })).toBeVisible()
  await expect(page.getByText('Waiting for assembly · Queued')).toBeVisible()
  expect(created.brief).toBe('Show our product')
  expect(created.scenes).toEqual([{ asset_id: assetId, duration_seconds: 5, caption: '' }])
  expect(created.request_key).toMatch(/^[0-9a-f-]{36}$/)
  await expect(page.locator('.video-scenes > details').first()).not.toHaveAttribute('open')
  await page.getByRole('button', { name: 'Assistant', exact: true }).click()
  await expect(page.getByText('Your video is queued.', { exact: true })).toBeVisible()
  expect(assistantContext.client_context.video_project_id).toBe(projectId)
  expect(assistantContext.page).toBe('video')
  await page.screenshot({ path: testInfo.outputPath('video-studio-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('video-studio-mobile.png'), fullPage: true })
})

test('Google narration adds a language, retries without another key and preserves the original version', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const projectId = '00000000-0000-4000-8000-000000000091'
  const assetId = '00000000-0000-4000-8000-000000000092'
  let created = false
  const requests = []
  let prepared
  const russian = { id: 'ru-1', version: 1, language: 'ru', voice: 'Kore', status: 'succeeded', stage: 'ready',
    output_url: '/russian-test.mp4', usage: { tokens_charged: 11 }, scenes: [{ asset_id: assetId, duration_seconds: 3,
      caption: 'Публикуйте быстрее', narration_text: 'Попробуйте Postbridge.', speech_asset_id: assetId, speech_url: '/speech-test.wav' }] }
  const english = { ...russian, id: 'en-2', version: 2, language: 'en', output_url: '/english-test.mp4',
    scenes: [{ ...russian.scenes[0], caption: 'Publish faster', narration_text: 'Try Postbridge today.' }] }
  const project = () => ({ id: projectId, brief: 'Product demo', current_version: created ? 2 : 1,
    current: created ? english : russian, revisions: created ? [english, russian] : [russian] })
  await page.route('**/workspaces/ws-1/videos**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path.endsWith('/capabilities')) return reply({ enabled: true, speech_enabled: true,
      speech_languages: ['ru', 'en'], speech_voices: ['Kore', 'Charon', 'Puck'], max_scenes: 8 })
    if (path.endsWith('/voice-variants')) {
      requests.push(route.request().postDataJSON()); created = true
      if (requests.length === 1) return route.abort('failed')
      return reply({ ...project(), requested_revision: english }, 201)
    }
    if (path.endsWith('/post-draft')) {
      prepared = route.request().postDataJSON()
      return reply({ project_id: projectId, version: 1, status: 'deleted' })
    }
    return reply(project())
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('postbridge_token', 'saas-token')
    window.localStorage.setItem('postbridge.locale', 'en')
  })
  await page.goto(`/workspaces/ws-1/content/videos/${projectId}`)
  await page.getByRole('button', { name: 'Add language', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Narration language', exact: true })).toHaveValue('en')
  await page.getByRole('button', { name: 'Create narrated version', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await page.getByRole('button', { name: 'Create narrated version', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Language and version', exact: true })).toHaveValue('2')
  expect(requests).toHaveLength(2)
  expect(requests[0]).toEqual(requests[1])
  expect(requests[0].source_version).toBe(1)
  expect(requests[0].language).toBe('en')
  await expect(page.locator('video').first()).toHaveAttribute('src', '/english-test.mp4')
  await page.getByRole('combobox', { name: 'Language and version', exact: true }).selectOption('1')
  await expect(page.locator('video').first()).toHaveAttribute('src', '/russian-test.mp4')
  await page.getByText('Scenes and settings', { exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Narration text', exact: true })).toHaveValue('Попробуйте Postbridge.')
  await page.getByRole('textbox', { name: 'Narration text', exact: true }).fill('Новая реплика.')
  await expect(page.getByRole('button', { name: 'Add language', exact: true })).toBeDisabled()
  await page.getByRole('textbox', { name: 'Narration text', exact: true }).fill('Попробуйте Postbridge.')
  // Reverting an edited transcript still requires reassembly to attach verified audio.
  await expect(page.getByRole('button', { name: 'Add language', exact: true })).toBeDisabled()
  await page.reload()
  await page.getByRole('combobox', { name: 'Language and version', exact: true }).selectOption('1')
  await page.getByRole('button', { name: 'Prepare post', exact: true }).click()
  await page.getByRole('button', { name: 'Create draft and open editor', exact: true }).click()
  await expect.poll(() => prepared).toEqual({ expected_version: 2, source_version: 1, caption: '' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByText('Scenes and settings', { exact: true }).click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('video-language-versions-mobile.png'), fullPage: true })
})

test('video autopilot accepts a task without files and exposes generated scenes for editing', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const projectId = '00000000-0000-4000-8000-000000000081'
  let created
  let assemblyObserved = false
  let edited = false
  let assistantRequest
  const scenes = [1, 2].map((number) => ({ asset_id: `00000000-0000-4000-8000-00000000008${number + 1}`,
    duration_seconds: 10, caption: `Generated scene ${number}`, kind: 'image' }))
  const project = (status) => {
    const current = { id: 'auto-revision', version: edited ? 2 : 1, mode: edited ? 'manual' : 'autopilot', status,
      stage: status === 'running' ? 'storyboard' : 'ready', scenes: status === 'running' ? [] : scenes,
      output_url: status === 'succeeded' ? '/video-test.mp4' : null, usage: { tokens_charged: status === 'succeeded' ? 50 : 0 } }
    return { id: projectId, brief: 'Show how publishing saves time', current_version: edited ? 2 : 1, current, revisions: [current] }
  }
  await page.route('**/workspaces/ws-1/assistant/help', async (route) => {
    assistantRequest = route.request().postDataJSON()
    if (assistantRequest.client_context.video_has_unsaved_changes) {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ conversation_id: 'video-edit',
        message: 'Save your manual edits first.', next_action: 'no_action' }) })
    }
    edited = true
    scenes[1].duration_seconds = 3
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ conversation_id: 'video-edit',
      message: 'Scene two is now three seconds.', next_action: 'open_video',
      route: `/workspaces/ws-1/content/videos/${projectId}`,
      video_update: { project_id: projectId, version: 2, status: 'succeeded' },
    }) })
  })
  await page.route('**/workspaces/ws-1/videos**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path.endsWith('/capabilities')) return reply({ enabled: true, autopilot_enabled: true,
      generated_media: true, max_upload_bytes: 67108864, max_scenes: 8 })
    if (route.request().method() === 'POST' && path.endsWith('/videos')) {
      created = route.request().postDataJSON()
      return reply(project('running'), 201)
    }
    if (path.endsWith(`/${projectId}`)) return reply(project(assemblyObserved ? 'succeeded' : 'running'))
    return reply({ items: [], has_more: false })
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('postbridge_token', 'saas-token')
    window.localStorage.setItem('postbridge.locale', 'en')
  })
  await page.goto('/workspaces/ws-1/content/videos/new')
  await page.getByLabel('What is your video about?').fill('Show how publishing saves time')
  await expect(page.getByText('Materials are optional.', { exact: false })).toBeVisible()
  await expect(page.getByText('Video duration', { exact: true })).not.toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('video-autopilot-create.png'), fullPage: true })
  await page.getByRole('button', { name: 'Create video', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/videos/${projectId}$`))
  await expect(page.getByText('Assembling · Preparing the storyboard')).toBeVisible()
  assemblyObserved = true
  expect(created.mode).toBe('autopilot')
  expect(created.language).toBe('en')
  expect(created.scenes).toEqual([])
  await expect(page.getByRole('link', { name: 'Download', exact: true })).toBeVisible()
  await expect(page.getByText('AI units used: 50', { exact: true })).toBeVisible()
  await page.getByText('Scenes and settings', { exact: true }).click()
  await expect(page.getByLabel('On-screen text').first()).toHaveValue('Generated scene 1')
  await expect(page.getByLabel('On-screen text')).toHaveCount(2)
  await page.getByLabel('On-screen text').first().fill('A better opening')
  await expect(page.getByRole('button', { name: 'Assemble video', exact: true })).toBeEnabled()
  await page.getByLabel('What would you like to change?').fill('Make scene two three seconds long')
  await expect(page.getByRole('button', { name: 'Edit with the assistant', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Assistant', exact: true }).click()
  await expect(page.getByText('Save your manual edits first.', { exact: true })).toBeVisible()
  expect(assistantRequest.client_context.video_has_unsaved_changes).toBe(true)
  await expect(page.getByLabel('On-screen text').first()).toHaveValue('A better opening')
  await page.getByLabel('On-screen text').first().fill('Generated scene 1')
  await page.getByRole('button', { name: 'Edit with the assistant', exact: true }).click()
  await expect(page.getByText('Scene two is now three seconds.', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Duration, seconds').nth(1)).toHaveValue('3')
  expect(assistantRequest.question).toBe('Make scene two three seconds long')
  expect(assistantRequest.client_context.video_project_id).toBe(projectId)
  await page.screenshot({ path: testInfo.outputPath('video-autopilot-edited.png'), fullPage: true })
})

test('completed video prepares one editable post and recovers a lost response', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const projectId = '00000000-0000-4000-8000-000000000061'
  const postId = '00000000-0000-4000-8000-000000000062'
  const assetId = '00000000-0000-4000-8000-000000000063'
  const outputUrl = '/video-handoff.mp4'
  let receipt = null
  const preparations = []
  const postWrites = []
  const reply = (route, body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/video-handoff.mp4', (route) => route.fulfill({ contentType: 'video/mp4', body: '' }))
  await page.route(`**/workspaces/ws-1/posts/${postId}`, (route) => {
    if (route.request().method() !== 'GET') postWrites.push(route.request().postDataJSON())
    return reply(route, { id: postId, content_md: 'Try our demo', status: 'draft', media_url: outputUrl,
      created_at: '2026-10-19T00:00:00Z', updated_at: '2026-10-19T00:00:00Z',
      video_origin: { project_id: projectId, version: 1, output_asset_id: assetId, output_url: outputUrl } })
  })
  await page.route('**/workspaces/ws-1/videos**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/capabilities')) return reply(route, { enabled: true, max_scenes: 8 })
    if (path.endsWith('/post-draft')) {
      preparations.push(route.request().postDataJSON())
      receipt = { content_item_id: postId, status: 'draft' }
      if (preparations.length === 1) return route.abort('failed')
      return reply(route, { ...receipt, project_id: projectId, version: 1 })
    }
    const current = { id: 'revision', version: 1, status: 'succeeded', scenes: [
      { asset_id: assetId, duration_seconds: 5, caption: 'On-screen caption', kind: 'image' },
    ], output_url: outputUrl, usage: {}, post_draft: receipt }
    return reply(route, { id: projectId, brief: 'Private production instructions', current_version: 1, current, revisions: [current] })
  })
  await page.addInitScript(() => {
    localStorage.setItem('postbridge_token', 'saas-token')
    localStorage.setItem('postbridge.locale', 'en')
  })
  await page.goto(`/workspaces/ws-1/content/videos/${projectId}`)
  await page.getByText('Scenes and settings', { exact: true }).click()
  await page.getByLabel('On-screen text').fill('Unsaved change')
  await expect(page.getByRole('button', { name: 'Prepare post', exact: true })).toBeDisabled()
  await page.getByLabel('On-screen text').fill('On-screen caption')
  await page.getByRole('button', { name: 'Prepare post', exact: true }).click()
  await expect(page.getByLabel('Post text (optional)')).toHaveValue('')
  await page.getByLabel('Post text (optional)').fill('Try our demo')
  await page.screenshot({ path: testInfo.outputPath('video-post-draft-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('video-post-draft-mobile.png'), fullPage: true })
  const prepare = page.getByRole('button', { name: 'Create draft and open editor', exact: true })
  await prepare.click()
  await expect(page.getByRole('alert')).toBeVisible()
  await prepare.click()
  await expect(page).toHaveURL(new RegExp(`/content/${postId}$`))
  await expect(page.getByRole('link', { name: 'Source video · version 1', exact: true })).toBeVisible()
  await expect(page.getByLabel('Video preview')).toBeVisible()
  await expect(page.getByLabel('Media URL', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Generate', exact: true })).toHaveCount(0)
  expect(preparations).toEqual([{ expected_version: 1, caption: 'Try our demo' }, { expected_version: 1, caption: 'Try our demo' }])
  expect(postWrites).toEqual([])
  await page.getByRole('link', { name: 'Source video · version 1', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Open post', exact: true })).toHaveAttribute('href', `/workspaces/ws-1/content/${postId}`)
  await expect(page.getByRole('button', { name: 'Prepare post', exact: true })).toHaveCount(0)
})

test('video delivery requires a fresh review and shows each destination result', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const postId = '00000000-0000-4000-8000-000000000051'
  const projectId = '00000000-0000-4000-8000-000000000052'
  let approved = false
  let retried = false
  let approvalBody
  let saved = 0
  const post = { id: postId, content_md: 'Exact caption', title: 'A video', status: 'draft', media_url: '/approval-video.mp4',
    video_origin: { project_id: projectId, version: 1, output_asset_id: 'asset', output_url: '/approval-video.mp4' },
    created_at: '2026-10-19T00:00:00Z', updated_at: '2026-10-19T00:00:00Z' }
  const view = () => ({ enabled: true, snapshot_hash: 'a'.repeat(64), approval_id: approved ? 'approval' : null,
    snapshot: { text: `${post.title}\n\n${post.content_md}`, version: 1, output_url: post.media_url },
    destinations: [{ id: 'first', title: 'First channel' }, { id: 'second', title: 'Second channel' }],
    targets: approved ? [{ id: 'target-first', title: 'First channel', status: 'published', external_url: 'https://t.me/demo/7' },
      { id: 'target-second', title: 'Second channel', status: retried ? 'unknown' : 'failed', error_code: 'EXTERNAL_API_TELEGRAM_PUBLISH_ERROR' }] : [] })
  const reply = (route, body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/approval-video.mp4', (route) => route.fulfill({ contentType: 'video/mp4', body: '' }))
  await page.route(`**/workspaces/ws-1/posts/${postId}`, (route) => {
    if (route.request().method() === 'PATCH') { saved++; Object.assign(post, route.request().postDataJSON()) }
    return reply(route, post)
  })
  await page.route('**/workspaces/ws-1/videos/posts/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/approve')) { approved = true; approvalBody = route.request().postDataJSON() }
    if (path.endsWith('/retry')) { expect(path).toContain('/target-second/retry'); retried = true }
    return reply(route, view())
  })
  await page.addInitScript(() => {
    localStorage.setItem('postbridge_token', 'saas-token')
    localStorage.setItem('postbridge.locale', 'en')
  })
  await page.goto(`/workspaces/ws-1/content/${postId}`)
  const card = page.getByRole('region', { name: 'Video delivery', exact: true })
  await expect(page.locator('.support-assistant-widget')).toHaveClass(/is-collapsed/)
  await expect(page.getByRole('button', { name: 'Publish', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Schedule publication')).toHaveCount(0)
  await card.getByRole('button', { name: 'Save and review delivery', exact: true }).click()
  await expect(card.locator('pre')).toHaveText('A video\n\nExact caption')
  await card.getByLabel('First channel · Telegram').check()
  await card.getByLabel('Second channel · Telegram').check()
  await page.locator('#title').fill('A revised title')
  await expect(card.getByRole('button', { name: 'Confirm and send', exact: true })).toBeDisabled()
  await card.getByRole('button', { name: 'Save and review delivery', exact: true }).click()
  await expect(card.locator('pre')).toHaveText('A revised title\n\nExact caption')
  await card.screenshot({ path: testInfo.outputPath('video-publication-desktop.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await card.screenshot({ path: testInfo.outputPath('video-publication-mobile.png') })
  await card.getByRole('button', { name: 'Confirm and send', exact: true }).click()
  expect(approvalBody).toEqual({ expected_snapshot: 'a'.repeat(64), channel_ids: ['first', 'second'], options: { first: { format: 'video' }, second: { format: 'video' } } })
  expect(saved).toBe(2)
  await expect(page.locator('#title')).toBeDisabled()
  await expect(card.getByRole('link', { name: 'Open publication', exact: true })).toHaveAttribute('href', 'https://t.me/demo/7')
  await card.getByRole('button', { name: 'Retry rejected delivery', exact: true }).click()
  await expect(card.getByText('Check delivery result', { exact: false })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Retry rejected delivery', exact: true })).toHaveCount(0)
})

test('approved video supports local delivery time and an idempotent reschedule', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const postId = '00000000-0000-4000-8000-000000000041'
  const projectId = '00000000-0000-4000-8000-000000000042'
  const day = new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10)
  let approved = false
  let missed = false
  let cancelled = false
  let approvalBody
  let delivery
  const moves = []
  const post = { id: postId, content_md: 'A scheduled video', status: 'draft', media_url: '/scheduled-video.mp4',
    video_origin: { project_id: projectId, version: 1, output_asset_id: 'asset', output_url: '/scheduled-video.mp4' },
    created_at: '2026-10-19T00:00:00Z', updated_at: '2026-10-19T00:00:00Z' }
  const view = () => ({ enabled: true, snapshot_hash: 'a'.repeat(64), approval_id: approved ? 'approval' : null,
    snapshot: { text: post.content_md, version: 1, output_url: post.media_url }, delivery,
    destinations: [{ id: 'target-channel', title: 'Video channel' }],
    targets: approved ? [{ id: 'target', title: 'Video channel', status: cancelled ? 'cancelled' : missed ? 'expired' : 'pending', error_code: missed ? 'VIDEO_DELIVERY_MISSED' : null }] : [] })
  const reply = (route, body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/scheduled-video.mp4', (route) => route.fulfill({ contentType: 'video/mp4', body: '' }))
  await page.route(`**/workspaces/ws-1/posts/${postId}`, (route) => reply(route, post))
  await page.route('**/workspaces/ws-1/videos/posts/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/approve')) {
      approvalBody = route.request().postDataJSON(); approved = true
      delivery = { version: 1, publish_at: approvalBody.publish_at, timezone: approvalBody.timezone,
        deadline_at: new Date(new Date(approvalBody.publish_at).getTime() + 900000).toISOString(),
        history: [{ version: 1, publish_at: approvalBody.publish_at, timezone: approvalBody.timezone }] }
    }
    if (path.endsWith('/reschedule')) {
      const body = route.request().postDataJSON(); moves.push(body)
      if (moves.length === 1) {
        delivery = { ...delivery, version: 2, publish_at: body.publish_at,
          deadline_at: new Date(new Date(body.publish_at).getTime() + 900000).toISOString(),
          history: [...delivery.history, { version: 2, publish_at: body.publish_at, timezone: body.timezone }] }
        missed = false
        return route.abort('failed')
      }
    }
    if (path.endsWith('/cancel')) cancelled = true
    return reply(route, view())
  })
  await page.addInitScript(() => {
    localStorage.setItem('postbridge_token', 'saas-token')
    localStorage.setItem('postbridge.locale', 'en')
  })
  await page.goto(`/workspaces/ws-1/content/${postId}`)
  const card = page.getByRole('region', { name: 'Video delivery', exact: true })
  await card.getByRole('button', { name: 'Save and review delivery', exact: true }).click()
  await expect(card.getByLabel('Delivery date and time')).toHaveCount(0)
  await card.getByLabel('Video channel · Telegram').check()
  await card.getByLabel('Send later', { exact: true }).check()
  await expect(card.getByRole('button', { name: 'Confirm and schedule', exact: true })).toBeDisabled()
  await card.getByLabel('Delivery timezone').selectOption('Asia/Novosibirsk')
  await card.getByLabel('Delivery date and time').fill(`${day}T09:15`)
  await card.screenshot({ path: testInfo.outputPath('video-delivery-time-desktop.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await card.screenshot({ path: testInfo.outputPath('video-delivery-time-mobile.png') })
  await card.getByRole('button', { name: 'Confirm and schedule', exact: true }).click()
  expect(approvalBody).toEqual({ expected_snapshot: 'a'.repeat(64), channel_ids: ['target-channel'], options: { 'target-channel': { format: 'video' } },
    publish_at: new Date(`${day}T09:15:00+07:00`).toISOString(), timezone: 'Asia/Novosibirsk' })
  missed = true
  await page.reload()
  await expect(card.getByText('Delivery window missed', { exact: false })).toBeVisible()
  await card.getByRole('button', { name: 'Change delivery time', exact: true }).click()
  await card.getByLabel('Delivery date and time').fill(`${day}T10:15`)
  const confirm = card.getByRole('button', { name: 'Confirm new time', exact: true })
  await confirm.click()
  await expect(card.getByRole('alert')).toBeVisible()
  await confirm.click()
  await expect(confirm).toHaveCount(0)
  expect(moves).toEqual([{ expected_schedule_version: 1, publish_at: new Date(`${day}T10:15:00+07:00`).toISOString(), timezone: 'Asia/Novosibirsk' },
    { expected_schedule_version: 1, publish_at: new Date(`${day}T10:15:00+07:00`).toISOString(), timezone: 'Asia/Novosibirsk' }])
  await card.getByRole('button', { name: 'Cancel deliveries still queued', exact: true }).click()
  await expect(card.getByText('Cancelled', { exact: false })).toBeVisible()
})

test('video schedules use assistant review cards and retain pause and draft history', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const taskId = '00000000-0000-4000-8000-000000000091'
  const projectId = '00000000-0000-4000-8000-000000000092'
  const runId = '00000000-0000-4000-8000-000000000093'
  let task
  let question
  let applied = 0
  let directCreates = 0
  const reply = (route, body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/workspaces/ws-1/videos**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/schedules')) return reply(route, { items: task ? [task] : [] })
    if (path.endsWith('/capabilities')) return reply(route, { enabled: true, autopilot_enabled: true,
      supports_schedules: true, max_upload_bytes: 67108864, max_scenes: 8 })
    const current = { id: 'revision', version: 1, status: 'succeeded', scenes: [], usage: {} }
    return reply(route, { id: projectId, brief: 'A weekly product video', current_version: 1, current,
      revisions: [current], origin: { agent_task_id: taskId, agent_run_id: runId } })
  })
  await page.route('**/workspaces/ws-1/agent/**', (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/tasks') && request.method() === 'POST') directCreates += 1
    if (path.endsWith('/runs')) return reply(route, { items: task ? [{ id: runId, agent_task_id: taskId,
      graph_name: 'video_autopilot', status: 'awaiting_review', created_at: '2026-10-19T02:30:00Z',
      video_execution: { project_id: projectId } }] : [] })
    if (path.endsWith('/pause')) { task.status = 'paused'; return reply(route, task) }
    return reply(route, { items: [] })
  })
  await page.route('**/workspaces/ws-1/assistant/help', (route) => {
    question = route.request().postDataJSON()
    return reply(route, { conversation_id: 'schedule-review', message: 'Review this schedule.', video_action: {
      id: '42', kind: 'schedule', status: 'proposed', summary: { brief: 'A weekly product video', frequency: 'daily',
        time: '09:30', timezone: 'Asia/Novosibirsk', max_units_per_month: 1000, language: 'en', count: 1,
        material_folder: 'Product', material_limit: 3, variants: [{ language: 'ru', voice: 'Kore', voice_style: 'warm' }] } } })
  })
  await page.route('**/workspaces/ws-1/assistant/actions/42/apply', (route) => {
    applied += 1
    task = { id: taskId, goal_text: 'A weekly product video', status: 'active', timezone: 'Asia/Novosibirsk',
      next_run_at: '2026-10-20T02:30:00Z' }
    return reply(route, { route: `/workspaces/ws-1/agents/videos?task=${taskId}` })
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('postbridge_token', 'saas-token')
    window.localStorage.setItem('postbridge.locale', 'en')
  })
  await page.goto('/workspaces/ws-1/agents/videos')
  await expect(page.getByRole('heading', { name: 'Ask the assistant to create videos' })).toBeVisible()
  await expect(page.locator('.video-studio select, .video-studio input[type=number]')).toHaveCount(0)
  await page.getByLabel('What should the assistant do?').fill('Create from the Product folder daily at 09:30 Novosibirsk in English and Russian; up to 1000 units a month.')
  await page.locator('.video-studio form').getByRole('button', { name: 'Assistant', exact: true }).click()
  await expect(page.getByText('Proposed schedule', { exact: true })).toBeVisible()
  expect(question.page).toBe('video')
  expect(question.question).toContain('Product folder')
  expect(applied).toBe(0)
  expect(directCreates).toBe(0)
  await expect(page.getByText('Folder: Product · Up to 3 materials per run', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('video-schedule-review.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('video-schedule-mobile.png'), fullPage: true })
  await page.getByRole('button', { name: 'Enable schedule', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'A weekly product video', exact: true })).toBeVisible()
  expect(applied).toBe(1)
  await page.getByRole('button', { name: 'Pause', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Edit with assistant', exact: true }).click()
  await page.getByLabel('What should the assistant do?').fill('Resume this schedule')
  await page.locator('.video-studio form').getByRole('button', { name: 'Assistant', exact: true }).click()
  await expect.poll(() => question.question).toContain('A weekly product video')
  await expect.poll(() => question.question).toContain('Resume this schedule')
  await page.getByRole('button', { name: 'Collapse', exact: true }).click()
  await expect(page.getByText('Ready to review', { exact: false })).toBeVisible()
  await page.getByRole('link', { name: 'Open project', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/content/videos/${projectId}$`))
  await expect(page.getByRole('link', { name: 'Open schedule', exact: true })).toHaveAttribute('href', `/workspaces/ws-1/agents/videos?task=${taskId}`)
})

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
    window.sessionStorage.setItem('postbridge.oauth_callback_expected', String(Date.now()))
  }, returnTo)

  await page.goto('/#token=oauth-session-token')

  await expect(page).toHaveURL(new RegExp(`${returnTo.replace(/[?]/g, '\\?')}$`))
  await expect(page.getByRole('heading', { name: 'OAuth consent reached' })).toBeVisible()
})

test('clears stale MCP return_to when opening login without return_to query', async ({ page }) => {
  const staleReturnTo = '/oauth/authorize?response_type=code&client_id=mcp-test&state=old'
  await mockSaasBff(page)
  await page.addInitScript((value) => {
    window.sessionStorage.setItem('postbridge.auth_return_to', value)
  }, staleReturnTo)

  await page.goto('/login')

  await expect
    .poll(() => page.evaluate(() => window.sessionStorage.getItem('postbridge.auth_return_to')))
    .toBeNull()
})

test('does not redirect OAuth hash logins to a stale MCP return_to', async ({ page }) => {
  const staleReturnTo = '/oauth/authorize?response_type=code&client_id=mcp-test&state=old'
  const calls = []
  await mockSaasBff(page, calls)

  await page.goto('/login')
  await page.evaluate((value) => {
    window.sessionStorage.setItem('postbridge.auth_return_to', value)
  }, staleReturnTo)
  await page.reload()
  await expect
    .poll(() => page.evaluate(() => window.sessionStorage.getItem('postbridge.auth_return_to')))
    .toBeNull()
  await page.evaluate(() => {
    window.sessionStorage.setItem('postbridge.oauth_callback_expected', String(Date.now()))
  })
  await page.goto('/dashboard#token=oauth-session-token')

  await expect(page).toHaveURL(/\/workspaces\/ws-1\/content$/)
  await expect(page.getByRole('heading', { name: 'Content' })).toBeVisible()
})

test('renders the configured Yandex login provider', async ({ page }) => {
  await mockSaasBff(page)

  await page.goto('/login')

  const yandexLogin = page.locator('a[href="/auth/yandex/start"]')
  await expect(yandexLogin).toBeVisible()
  await expect(yandexLogin).toContainText('Yandex')
})

test('accepts a legacy OAuth callback token on the dashboard route', async ({ page }) => {
  const calls = []
  await mockSaasBff(page, calls)
  await page.addInitScript(() => {
    window.sessionStorage.setItem('postbridge.oauth_callback_expected', String(Date.now()))
  })

  await page.goto('/dashboard#token=oauth-session-token')

  await expect(page).toHaveURL(/\/workspaces\/ws-1\/content$/)
  await expect(page.getByRole('heading', { name: 'Content' })).toBeVisible()
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem('postbridge_token')))
    .toBe('oauth-session-token')
  expect(calls).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ path: '/me', method: 'GET', authorization: 'Bearer oauth-session-token' }),
    ]),
  )
})

test('ignores crafted OAuth callback tokens without a pending provider login', async ({ page }) => {
  await mockSaasBff(page)

  await page.goto('/dashboard#token=oauth-session-token')

  await expect(page).toHaveURL('/dashboard')
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem('postbridge_token')))
    .toBeNull()
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

test('shows the worldwide IANA timezone catalog for the IO market', async ({ page }) => {
  const calls = []
  await mockSaasBff(page, calls)
  await page.addInitScript(() => {
    window.localStorage.setItem('postbridge_token', 'saas-token')
    window.localStorage.setItem('postbridge.locale', 'en')
  })

  await page.goto('/')
  await expect(page).toHaveURL(/\/workspaces\/ws-1\/content$/)
  await page.getByRole('link', { name: 'Settings', exact: true }).click()
  await expect(page).toHaveURL(/\/workspaces\/ws-1\/settings$/)

  const timezoneSelect = page.getByLabel('Time zone')
  await expect(timezoneSelect).toBeVisible()
  const options = timezoneSelect.locator('option')
  await expect(options).toHaveCount(40)
  await expect(timezoneSelect.locator('option[value="UTC"]')).toHaveText(
    '(UTC+00:00) Coordinated Universal Time',
  )
  await expect(timezoneSelect.locator('option[value="Europe/London"]')).toContainText(
    'United Kingdom & Ireland Time',
  )
  await expect(timezoneSelect.locator('option[value="America/New_York"]')).toContainText(
    'Eastern Time — US & Canada',
  )
  await expect(timezoneSelect.locator('option[value="Asia/Tokyo"]')).toContainText(
    'Japan Standard Time',
  )
  expect((await options.allTextContents()).every((label) => !label.includes('/'))).toBe(true)
  await expect(options.filter({ hasText: /MSK[+−]/ })).toHaveCount(0)

  const russianCatalog = await page.evaluate(async () => {
    const { getTimezoneSelectOptions } = await import('/src/timezoneOptions.js')
    return getTimezoneSelectOptions((key) => (key === 'timezone.msk' ? 'MSK' : key), 'ru')
  })
  expect(russianCatalog).toHaveLength(11)
  expect(russianCatalog.every(({ label }) => label.includes('MSK'))).toBe(true)
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

test('storyboard changes only the selected illustration and preserves scene settings', async ({ page }) => {
  await mockSaasBff(page)
  const projectId = '00000000-0000-4000-8000-000000000091'
  const aid = '00000000-0000-4000-8000-000000000092'
  let assembled
  const scenes = [{ asset_id: aid, duration_seconds: 5, caption: 'Opening', kind: 'image', scene_id: 'opening', motion: 'none', image_prompt: 'An office' },
    { asset_id: aid, duration_seconds: 5, caption: 'Try it', kind: 'image', scene_id: 'ending', purpose: 'cta' }]
  let current = { version: 1, status: 'succeeded', mode: 'manual', stage: 'ready', language: 'en', scenes }
  const project = () => ({ id: projectId, brief: 'Story demo', current_version: current.version, current, revisions: [current] })
  await page.route('**/workspaces/ws-1/videos**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const reply = (body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (path.endsWith('/capabilities')) return reply({ enabled: true, generated_media: true, max_scenes: 8, max_upload_bytes: 67108864 })
    if (path.endsWith('/materials')) return reply({ items: [], folders: [], has_more: false })
    if (path.endsWith('/revisions')) { assembled = route.request().postDataJSON(); current = { ...current, version: 2, scenes: assembled.scenes, status: 'queued' } }
    return reply(project())
  })
  await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
  await page.goto(`/workspaces/ws-1/content/videos/${projectId}`)
  await expect(page.getByRole('combobox', { name: 'Image motion', exact: true }).first()).not.toBeVisible()
  await page.locator('.video-scenes > .video-library > summary').click()
  await page.getByRole('searchbox', { name: 'Find material' }).fill('office')
  await page.getByRole('searchbox', { name: 'Find material' }).press('Enter')
  expect(assembled).toBeUndefined()
  await page.locator('.video-scenes > .video-library > summary').click()
  await page.getByText('Scenes and settings', { exact: true }).click()
  await page.getByText('Scene editing and replacement', { exact: true }).first().click()
  await page.getByRole('combobox', { name: 'Image motion', exact: true }).first().selectOption('zoom_in')
  await page.getByRole('combobox', { name: 'Transition', exact: true }).first().selectOption('fade')
  await page.getByLabel('Generate a new AI illustration', { exact: true }).first().check()
  await page.getByRole('textbox', { name: 'Image description', exact: true }).fill('A calm blue office illustration')
  await page.getByRole('button', { name: 'Assemble video', exact: true }).click()
  await expect.poll(() => assembled?.scenes[0]?.regenerate_image).toBe(true)
  expect(assembled.scenes[0]).toMatchObject({ motion: 'zoom_in', transition: 'fade', scene_id: 'opening' })
  expect(assembled.scenes[1]).toMatchObject({ asset_id: aid, caption: 'Try it', scene_id: 'ending' })
  expect(assembled.scenes[1].regenerate_image).toBeUndefined()
})

test('assistant schedule card waits for explicit apply', async ({ page }) => {
  await mockSaasBff(page)
  const projectId = '00000000-0000-4000-8000-000000000093'
  let applied = 0
  let asked
  const current = { version: 1, status: 'succeeded', mode: 'manual', stage: 'ready', scenes: [], language: 'en' }
  await page.route('**/workspaces/ws-1/videos**', (route) => {
    const body = new URL(route.request().url()).pathname.endsWith('/capabilities') ? { enabled: true, max_scenes: 8 } : { id: projectId, brief: 'Weekly demo', current_version: 1, current, revisions: [current] }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.route('**/workspaces/ws-1/assistant/help', (route) => {
    asked = route.request().postDataJSON()
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ conversation_id: 'schedule-card', message: 'Review this schedule.',
      video_action: { id: '42', kind: 'schedule', status: 'proposed', summary: { brief: 'Weekly demo', version: 1, frequency: 'daily', time: '09:00', timezone: 'Asia/Novosibirsk', max_units_per_run: 5000, max_units_per_month: 50000 } } }) })
  })
  await page.route('**/workspaces/ws-1/assistant/actions/42/apply', (route) => {
    applied++; return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ route: `/workspaces/ws-1/content/videos/${projectId}` }) })
  })
  await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
  await page.goto(`/workspaces/ws-1/content/videos/${projectId}`)
  await page.getByRole('button', { name: 'Assistant', exact: true }).click()
  await expect(page.getByText('Proposed schedule', { exact: true })).toBeVisible()
  await expect(page.getByText('Every day · 09:00 · Asia/Novosibirsk', { exact: true })).toBeVisible()
  expect(applied).toBe(0); expect(asked.request_key).toBeTruthy()
  await page.getByRole('button', { name: 'Enable schedule', exact: true }).click()
  await expect.poll(() => applied).toBe(1)
  await expect(page.getByRole('button', { name: 'Applied', exact: true })).toBeDisabled()
})

for (const kind of ['video_generation', 'content_generation']) {
  test(`assistant ${kind} shows the workspace reserve before any paid action`, async ({ page }) => {
    await mockSaasBff(page)
    const projectId = '00000000-0000-4000-8000-000000000094'
    let applied = 0
    const current = { version: 1, status: 'succeeded', mode: 'manual', stage: 'ready', scenes: [], language: 'en' }
    await page.route('**/workspaces/ws-1/videos**', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(
      new URL(route.request().url()).pathname.endsWith('/capabilities') ? { enabled: true } : { id: projectId, brief: 'Product demo', current_version: 1, current, revisions: [current] }) }))
    await page.route('**/workspaces/ws-1/assistant/help', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      conversation_id: 'paid-card', message: 'Review the reserve.', video_action: { id: '44', kind, status: 'proposed',
        summary: { brief: 'Create a product demo', language: 'en', reserve_units: 10000, ...(kind === 'content_generation' ? { task: 'script' } : {}) } } }) }))
    await page.route('**/workspaces/ws-1/assistant/actions/44/apply', (route) => {
      applied++
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(kind === 'content_generation'
        ? { content_result: { text: 'Hook: show the finished video.', charged_units: 155, billing_pending: false } }
        : { route: `/workspaces/ws-1/content/videos/${projectId}`, video_update: { project_id: projectId, version: 2, status: 'queued' } }) })
    })
    await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
    await page.goto(`/workspaces/ws-1/content/videos/${projectId}`)
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(page.getByText('Generation reserve: 10000 AI units', { exact: true })).toBeVisible()
    await expect(page.getByText('Language: en', { exact: true })).toBeVisible()
    await expect(page.getByText('This is a preliminary reserve, not an exact price.', { exact: false })).toBeVisible()
    expect(applied).toBe(0)
    await page.getByRole('button', { name: 'Generate', exact: true }).click()
    await expect.poll(() => applied).toBe(1)
    await expect(page.getByRole('button', { name: 'Applied', exact: true })).toBeDisabled()
    if (kind === 'content_generation') await expect(page.getByText('Hook: show the finished video.', { exact: true })).toBeVisible()
  })
}

test('video delivery reveals YouTube metadata on selection and reconciles the saved upload', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const postId = '00000000-0000-4000-8000-000000000091'
  let approved = false
  let reconciled = false
  let body
  const post = { id: postId, title: 'Clip', content_md: 'Exact description', status: 'draft', media_url: '/short.mp4', video_origin: { project_id: 'project', version: 1, output_url: '/short.mp4' } }
  const view = () => ({ enabled: true, snapshot_hash: 'a'.repeat(64), approval_id: approved ? 'approval' : null,
    snapshot: { version: 1, output_url: '/short.mp4', text: 'Clip\n\nExact description' },
    destinations: [{ id: 'youtube-channel', title: 'My channel', platform: 'youtube', format: 'short' }, { id: 'instagram-channel', title: 'My Instagram', platform: 'instagram', format: 'reel' }],
    targets: approved ? [{ id: 'target', title: 'My channel', platform: 'youtube', format: 'short', status: reconciled ? 'processing' : 'unknown' }] : [] })
  const reply = (route, value) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) })
  await page.route('**/short.mp4', route => route.fulfill({ contentType: 'video/mp4', body: '' }))
  await page.route(`**/workspaces/ws-1/posts/${postId}`, route => reply(route, post))
  await page.route('**/workspaces/ws-1/videos/posts/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/approve')) { approved = true; body = route.request().postDataJSON() }
    if (path.endsWith('/reconcile')) reconciled = true
    return reply(route, view())
  })
  await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
  await page.goto(`/workspaces/ws-1/content/${postId}`)
  const card = page.getByRole('region', { name: 'Video delivery', exact: true })
  await card.getByRole('button', { name: 'Save and review delivery', exact: true }).click()
  await expect(card.getByLabel('Video title', { exact: true })).toHaveCount(0)
  await card.getByLabel('My channel · YouTube · Short', { exact: true }).check()
  await expect(card.getByLabel('Visibility', { exact: true })).toHaveValue('private')
  const confirm = card.getByRole('button', { name: 'Confirm and send', exact: true })
  await expect(confirm).toBeDisabled()
  await card.getByLabel('Video title', { exact: true }).fill('Approved YouTube title')
  await expect(confirm).toBeDisabled()
  await card.getByLabel('Video audience', { exact: true }).selectOption('false')
  await card.getByLabel('My Instagram · Instagram · Reel', { exact: true }).check()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await card.screenshot({ path: testInfo.outputPath('youtube-video-options-mobile.png') })
  await confirm.click()
  expect(body.options).toEqual({ 'youtube-channel': { format: 'short', title: 'Approved YouTube title', visibility: 'private', made_for_kids: false, contains_synthetic_media: true }, 'instagram-channel': { format: 'reel' } })
  await card.getByRole('button', { name: 'Check existing upload', exact: true }).click()
  await expect(card.getByText('Platform is processing the video', { exact: false })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Check existing upload', exact: true })).toHaveCount(0)
})

test('TikTok review has no default visibility and requires current creator settings and declarations', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  const postId = '00000000-0000-4000-8000-000000000093'
  const coreId = '00000000-0000-4000-8000-000000000094'
  let body
  let creatorRequests = 0
  let approved = false
  const reply = (route, value) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) })
  const view = () => ({ enabled: true, source_duration_seconds: 10, snapshot_hash: 'a'.repeat(64), approval_id: approved ? 'approval' : null,
    snapshot: { version: 1, output_url: '/short.mp4', text: 'Exact creator caption' }, destinations: [],
    bridges: [{ id: 'tiktok-bridge', channel_id: coreId, title: 'TikTok creator', platform: 'tiktok', format: 'tiktok', settings_hash: 'b'.repeat(64), options: { format: 'tiktok', contains_synthetic_media: true } }], targets: [] })
  await page.route('**/short.mp4', route => route.fulfill({ contentType: 'video/mp4', body: '' }))
  await page.route(`**/workspaces/ws-1/posts/${postId}`, route => reply(route, { id: postId, title: 'Clip', content_md: 'Exact creator caption', status: 'draft', media_url: '/short.mp4', video_origin: { version: 1, output_url: '/short.mp4' } }))
  await page.route('**/workspaces/ws-1/videos/posts/**', route => {
    if (new URL(route.request().url()).pathname.endsWith('/approve')) { approved = true; body = route.request().postDataJSON() }
    return reply(route, view())
  })
  await page.route(`**/videos/tiktok/channels/${coreId}/creator-info`, route => {
    creatorRequests += 1
    return reply(route, { creator_nickname: 'Current nickname', creator_username: 'creator', privacy_level_options: ['PUBLIC_TO_EVERYONE', 'SELF_ONLY'],
      max_video_post_duration_sec: 180, comment_disabled: false, duet_disabled: true, stitch_disabled: false, app_audited: true })
  })
  await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
  await page.goto(`/workspaces/ws-1/content/${postId}`)
  const card = page.getByRole('region', { name: 'Video delivery', exact: true })
  await card.getByRole('button', { name: 'Save and review delivery', exact: true }).click()
  await card.getByLabel('TikTok creator · TikTok · TikTok video', { exact: true }).check()
  await expect(card.getByText('Current nickname', { exact: true })).toBeVisible()
  const visibility = card.getByLabel('Visibility', { exact: true })
  await expect(visibility).toHaveValue('')
  await expect(card.getByLabel('Allow comments', { exact: false })).not.toBeChecked()
  await expect(card.getByLabel('Allow Duet', { exact: false })).toBeDisabled()
  const confirm = card.getByRole('button', { name: 'Confirm and send', exact: true })
  await expect(confirm).toBeDisabled()
  await visibility.selectOption('SELF_ONLY')
  await card.getByLabel('This video promotes a brand, product or service', { exact: true }).check()
  await expect(card.getByLabel('Branded content (another brand)', { exact: true })).toBeDisabled()
  await expect(confirm).toBeDisabled()
  await card.getByLabel('Your brand', { exact: true }).check()
  await expect(card.getByText("Your video will be labeled as 'Promotional content'.", { exact: true })).toBeVisible()
  await card.getByLabel('I have the rights to share this original video on TikTok.', { exact: true }).check()
  await card.getByLabel("By posting, you agree to TikTok's Music Usage Confirmation.", { exact: true }).check()
  await expect(confirm).toBeEnabled()
  await visibility.selectOption('PUBLIC_TO_EVERYONE')
  await card.getByLabel('Branded content (another brand)', { exact: true }).check()
  await expect(confirm).toBeDisabled()
  await expect(card.getByRole('option', { name: 'Only me', exact: true })).toHaveAttribute('disabled', '')
  await card.getByLabel("By posting, you agree to TikTok's Branded Content Policy and Music Usage Confirmation.", { exact: true }).check()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await card.screenshot({ path: testInfo.outputPath('tiktok-per-post-review-mobile.png') })
  await confirm.click()
  expect(creatorRequests).toBe(1)
  expect(body).toMatchObject({ bridge_ids: ['tiktok-bridge'], expected_bridges: { 'tiktok-bridge': 'b'.repeat(64) },
    tiktok_options: { [coreId]: { privacy_level: 'PUBLIC_TO_EVERYONE', allow_comment: false, allow_duet: false, allow_stitch: false,
      commercial_content: true, brand_organic: true, branded_content: true, original_content_confirmed: true, music_usage_confirmed: true } } })
})

test('TikTok connects as a receiving channel with explicit policy acceptance', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
  await page.goto('/workspaces/ws-1/channels/add')
  await page.getByLabel('Platform', { exact: true }).selectOption('tiktok')
  const connect = page.getByRole('button', { name: 'Connect TikTok', exact: true })
  await expect(connect).toBeDisabled()
  await page.getByLabel(/I agree to the TikTok connection terms:/).check()
  await expect(connect).toBeEnabled()
  await page.screenshot({ path: testInfo.outputPath('tiktok-connection-policy.png'), fullPage: true })
})

test('YouTube connection requires policy acceptance before OAuth', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
  await page.goto('/workspaces/ws-1/channels/add')
  await page.getByLabel('Platform', { exact: true }).selectOption('youtube')
  const connect = page.getByRole('button', { name: 'Connect YouTube with Google', exact: true })
  await expect(connect).toBeDisabled()
  await expect(page.getByRole('link', { name: 'YouTube Terms', exact: true })).toHaveAttribute('href', 'https://www.youtube.com/t/terms')
  await page.getByRole('checkbox').check()
  await expect(connect).toBeEnabled()
  await page.getByRole('checkbox').uncheck()
  await expect(connect).toBeDisabled()
  await expect(page.getByLabel('Access token', { exact: true })).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('youtube-consent.png'), fullPage: true })
})

test('YouTube bridge configures the receiving side and can be edited from bridge settings', async ({ page }, testInfo) => {
  await mockSaasBff(page)
  let created
  let patched
  const registry = [
    { id: 'source', platform: 'postbridge', platform_channel_id: 'pb/ws-1', title: 'Editorial', can_read: true, can_write: false, live_sync_source_supported: true },
    { id: 'target', platform: 'youtube', platform_channel_id: 'UCtest', title: 'My YouTube', can_read: false, can_write: true, credentials_ref: 'core-youtube' },
  ]
  let bridge = { id: 'video-bridge', source_channel_id: 'source', target_channel_id: 'target', status: 'active', mode: 'live_sync', settings_json: {} }
  const reply = (route, data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) })
  await page.route('**/workspaces/ws-1/channel-registry', route => reply(route, { items: registry }))
  await page.route('**/workspaces/ws-1/dashboard/jobs', route => reply(route, { items: [] }))
  await page.route('**/workspaces/ws-1/onboarding/state', route => reply(route, null))
  await page.route('**/workspaces/ws-1/bridges', route => reply(route, { items: created ? [bridge] : [] }))
  await page.route('**/workspaces/ws-1/bridges/video-bridge', route => {
    patched = route.request().postDataJSON()
    bridge = { ...bridge, settings_json: { ...bridge.settings_json, video_delivery: patched.video_delivery } }
    return reply(route, bridge)
  })
  await page.route('**/workspaces/ws-1/connections/create', route => {
    created = route.request().postDataJSON()
    bridge.settings_json = { video_delivery: created.video_delivery }
    return reply(route, { bridge_id: bridge.id, job_id: null })
  })
  await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
  await page.goto('/workspaces/ws-1/migrate')
  await expect(page.locator('#target-channel-id')).toHaveValue('target')
  const create = page.getByRole('button', { name: 'Create bridge', exact: true })
  await expect(create).toBeDisabled()
  await expect(page.getByLabel('Visibility', { exact: true })).toHaveValue('private')
  await page.getByLabel('Video audience', { exact: true }).selectOption('false')
  await create.click()
  expect(created).toMatchObject({ source_platform: 'postbridge', target_platform: 'youtube', requested_limit: 0, target_credentials_id: 'core-youtube', video_delivery: { format: 'short', made_for_kids: false, visibility: 'private' } })
  await page.getByText('Bridge settings', { exact: true }).click()
  await expect(page.locator('.bridge-adaptation-settings')).toHaveCount(0)
  await expect(page.locator('.bridge-link-back-settings')).toHaveCount(0)
  await page.getByLabel('Visibility', { exact: true }).selectOption('unlisted')
  await page.getByRole('button', { name: 'Save video settings', exact: true }).click()
  expect(patched.video_delivery.visibility).toBe('unlisted')
  await expect(page.getByLabel('Visibility', { exact: true })).toHaveValue('unlisted')
  await page.screenshot({ path: testInfo.outputPath('youtube-bridge-settings.png'), fullPage: true })
})

test('studio delivery selects bridges and reviews receiving side settings without overriding them', async ({ page }) => {
  await mockSaasBff(page)
  const postId = '00000000-0000-4000-8000-000000000092'
  let body
  let approved = false
  const post = { id: postId, title: 'Clip title', content_md: 'Exact description', status: 'draft', media_url: '/short.mp4', video_origin: { project_id: 'project', version: 1, output_url: '/short.mp4' } }
  const view = () => ({ enabled: true, approval_id: approved ? 'approval' : null, snapshot_hash: 'a'.repeat(64),
    snapshot: { version: 1, output_url: '/short.mp4', text: 'Clip title\n\nExact description' },
    bridges: [{ id: 'bridge-youtube', title: 'My channel', platform: 'youtube', format: 'short', settings_hash: 'b'.repeat(64), options: { format: 'short', title: 'Clip title', visibility: 'private', made_for_kids: false } }],
    destinations: [{ id: 'unbridged', title: 'Must not be selectable', platform: 'youtube' }], targets: approved ? [{ id: 'paused-target', title: 'My channel', platform: 'youtube', format: 'short', status: 'failed', error_code: 'VIDEO_BRIDGE_UNAVAILABLE' }] : [] })
  const reply = (route, data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) })
  await page.route('**/short.mp4', route => route.fulfill({ contentType: 'video/mp4', body: '' }))
  await page.route(`**/workspaces/ws-1/posts/${postId}`, route => reply(route, post))
  await page.route('**/workspaces/ws-1/videos/posts/**', route => {
    if (new URL(route.request().url()).pathname.endsWith('/approve')) { body = route.request().postDataJSON(); approved = true }
    return reply(route, view())
  })
  await page.addInitScript(() => { localStorage.setItem('postbridge_token', 'saas-token'); localStorage.setItem('postbridge.locale', 'en') })
  await page.goto(`/workspaces/ws-1/content/${postId}`)
  const card = page.getByRole('region', { name: 'Video delivery', exact: true })
  await card.getByRole('button', { name: 'Save and review delivery', exact: true }).click()
  await expect(card.getByText('Send through bridges', { exact: true })).toBeVisible()
  await expect(card.getByText('Must not be selectable')).toHaveCount(0)
  await card.getByLabel('My channel · YouTube · Short', { exact: true }).check()
  await expect(card.getByRole('combobox')).toHaveCount(0)
  await expect(card.getByText('Private', { exact: false })).toBeVisible()
  await card.getByRole('button', { name: 'Confirm and send', exact: true }).click()
  expect(body).toEqual({ expected_snapshot: 'a'.repeat(64), bridge_ids: ['bridge-youtube'], expected_bridges: { 'bridge-youtube': 'b'.repeat(64) } })
  await expect(card.getByText('The bridge is paused, deleted or unavailable in this workspace.', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Check existing upload', exact: true })).toHaveCount(0)
  await expect(card.getByRole('link', { name: 'Manage bridges', exact: true })).toBeVisible()
})
