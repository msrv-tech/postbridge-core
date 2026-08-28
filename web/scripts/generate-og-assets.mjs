import { mkdir, readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(here, '..')
const outputDir = resolve(webRoot, 'public', 'og')
const mark = await readFile(resolve(webRoot, 'public', 'postbridge-mark.svg'), 'utf8')
const markDataUrl = `data:image/svg+xml;base64,${Buffer.from(mark).toString('base64')}`

const assets = [
  ['home.png', 'Social publishing, connected', 'Draft, schedule, bridge, and publish to global channels from one workspace.'],
  ['platforms.png', 'Supported platforms', 'Connect global social channels, feeds, and content sources.'],
  ['cases.png', 'Publishing use cases', 'Practical workflows for teams, creators, and agencies.'],
  ['mcp.png', 'Postbridge MCP', 'Drafts, media, schedules, bridges, and confirmed publishing from ChatGPT.'],
  ['pricing.png', 'Postbridge.io pricing', 'Free, Pro, and Agency plans for multi-platform publishing.'],
  ['platform-telegram.png', 'Publish to Telegram', 'Plan, schedule, and deliver channel posts from one workspace.'],
  ['platform-facebook.png', 'Publish to Facebook Pages', 'Prepare and publish content through a connected Meta account.'],
  ['platform-instagram.png', 'Publish to Instagram', 'Manage supported media posts alongside your other channels.'],
  ['platform-x.png', 'Publish to X', 'Adapt concise posts, schedule delivery, and track results.'],
  ['platform-linkedin.png', 'Publish to LinkedIn', 'Create professional updates and media from Postbridge.io.'],
  ['platform-bluesky.png', 'Publish to Bluesky', 'Add open social publishing to your shared content workflow.'],
  ['platform-mastodon.png', 'Publish to Mastodon', 'Connect your server and publish from the same calendar.'],
  ['platform-rss.png', 'RSS publishing workflows', 'Connect readable sources, feeds, and social distribution.'],
  ['platform-postbridge.png', 'Postbridge source', 'Use one source draft for bridges and multiple destinations.'],
  ['platform-max.png', 'Publish to MAX', 'Regional publishing when the provider is enabled.'],
  ['platform-vk.png', 'Publish to VK', 'Community publishing when the provider is enabled.'],
  ['platform-zen.png', 'Publish to Dzen', 'Channel publishing when the provider is enabled.'],
  ['case-multi-platform-publishing.png', 'Multi-platform publishing', 'One source draft for LinkedIn, X, Bluesky, Mastodon, and more.'],
  ['case-chatgpt-social-publishing.png', 'Publish from ChatGPT', 'Use Postbridge MCP with previews and explicit confirmation.'],
  ['case-telegram-to-linkedin-and-x.png', 'Telegram to LinkedIn and X', 'Reuse source content with destination-specific adaptation.'],
  ['case-facebook-instagram-publishing.png', 'Facebook and Instagram publishing', 'Plan Meta publications beside the rest of your channels.'],
  ['case-social-media-content-calendar.png', 'Social media content calendar', 'Coordinate drafts, schedules, destinations, and delivery status.'],
  ['case-bluesky-mastodon-crossposting.png', 'Bluesky and Mastodon', 'Cross-post to open social destinations from one workflow.'],
  ['case-rss-to-social-media.png', 'RSS to social media', 'Build repeatable feed and social publishing workflows.'],
]

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 })
await mkdir(outputDir, { recursive: true })

for (const [filename, title, description] of assets) {
  await page.setContent(`<!doctype html>
    <html><head><style>
      * { box-sizing: border-box; }
      html, body { width: 1200px; height: 630px; margin: 0; }
      body { font-family: Arial, Helvetica, sans-serif; background: #081426; color: #f8fafc; }
      main { position: relative; width: 100%; height: 100%; padding: 64px 72px; overflow: hidden; }
      .rail { position: absolute; inset: 0 auto 0 0; width: 18px; background: #2dd4bf; }
      .accent { position: absolute; right: 72px; top: 64px; width: 150px; height: 12px; background: #fbbf24; }
      .accent.secondary { top: 84px; width: 92px; background: #fb7185; }
      .brand { display: flex; align-items: center; gap: 18px; font-size: 27px; font-weight: 700; }
      .brand img { width: 52px; height: 52px; }
      .copy { position: absolute; left: 72px; right: 110px; bottom: 72px; }
      h1 { max-width: 980px; margin: 0 0 24px; font-size: 66px; line-height: 1.04; letter-spacing: 0; }
      p { max-width: 900px; margin: 0; color: #bfdbfe; font-size: 30px; line-height: 1.35; letter-spacing: 0; }
    </style></head><body><main>
      <div class="rail"></div><div class="accent"></div><div class="accent secondary"></div>
      <div class="brand"><img src="${markDataUrl}" alt="" />Postbridge.io</div>
      <div class="copy"><h1>${escapeHtml(title)}</h1><p>${escapeHtml(description)}</p></div>
    </main></body></html>`)
  await page.screenshot({ path: resolve(outputDir, filename), type: 'png' })
}

await browser.close()

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}
