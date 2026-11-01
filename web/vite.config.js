import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In Docker (environments/dev): VITE_API_PROXY_TARGET=http://saas-api:8001.
// Local host-dev SaaS API listens on :8001 by default.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8001'
const base = process.env.VITE_BASE_PATH || '/'
const publicBaseUrl = (process.env.VITE_POSTBRIDGE_PUBLIC_BASE_URL || 'https://postbridge.io').replace(/\/+$/, '')
const publicBrand = publicBaseUrl.includes('postbridge.ru') ? 'Postbridge.ru' : 'Postbridge.io'
const publicHostname = new URL(publicBaseUrl).hostname.toLowerCase()
const publicOperatorHtml = process.env.VITE_POSTBRIDGE_APP_MODE === 'saas'
  && (publicHostname === 'postbridge.io' || publicHostname.endsWith('.postbridge.io'))
  ? '<p>Postbridge.io is operated by ООО &quot;МАНЖЕЛА&quot;.</p>'
  : ''
const publicLocale = publicBrand === 'Postbridge.ru' ? 'ru' : 'en'
const publicCopy = publicLocale === 'ru' ? {
  title: 'ИИ-студия контента: тексты, изображения и видео',
  headline: 'Создавайте тексты, изображения и видео с ИИ',
  description: 'Postbridge — ИИ-студия контента: генерация текстов, изображений и коротких видео с озвучкой, редактура, расписание и публикация в подключённые каналы.',
  details: 'Начните с задачи или своих материалов. Создавайте сценарии, иллюстрации и видеосцены, добавляйте озвучку и субтитры на русском и английском. Скачивайте MP4 или готовьте публикацию.',
  features: ['Генерация текстов и редактура с ИИ', 'Генерация изображений', 'ИИ-видеосцены и монтаж коротких видео', 'Озвучка, субтитры и языковые версии RU/EN', 'Подготовка контента по расписанию', 'Публикация и кросспостинг', 'Интеграция MCP'],
  nav: '<a href="/#formats">Возможности</a> <a href="/#video-studio">Видеостудия</a> <a href="/docs/mcp">MCP</a> <a href="/pricing">Тарифы</a>',
} : {
  title: 'AI content studio: posts, images, and videos',
  headline: 'Create posts, images, and videos with AI',
  description: 'Postbridge is an AI content studio for posts, images, and short videos with voiceovers. Create, edit, schedule, and publish to your connected channels.',
  details: 'Start with a brief or your own materials. Create storyboards, illustrations, and AI video scenes, with Russian and English voiceovers and subtitles. Download an MP4 or prepare it for publishing.',
  features: ['AI writing and editing', 'Image generation', 'AI video scenes and short video production', 'Russian and English voiceovers, subtitles, and language versions', 'Recurring content preparation', 'Publishing and cross-posting', 'MCP integration'],
  nav: '<a href="/#formats">Features</a> <a href="/#video-studio">Video studio</a> <a href="/platforms">Platforms</a> <a href="/docs/mcp">MCP</a> <a href="/pricing">Pricing</a>',
}

const publicBaseUrlPlugin = {
  name: 'postbridge-public-base-url',
  transformIndexHtml(html) {
    return html
      .replaceAll('__POSTBRIDGE_PUBLIC_BASE_URL__', publicBaseUrl)
      .replaceAll('__POSTBRIDGE_BRAND__', publicBrand)
      .replaceAll('__POSTBRIDGE_TITLE__', publicCopy.title)
      .replaceAll('__POSTBRIDGE_LOCALE__', publicLocale)
      .replaceAll('__POSTBRIDGE_DESCRIPTION__', publicCopy.description)
      .replaceAll('__POSTBRIDGE_HEADLINE__', publicCopy.headline)
      .replaceAll('__POSTBRIDGE_DETAILS__', publicCopy.details)
      .replaceAll('__POSTBRIDGE_FEATURES_JSON__', JSON.stringify(publicCopy.features))
      .replaceAll('__POSTBRIDGE_NAV_HTML__', publicCopy.nav)
      .replaceAll('__POSTBRIDGE_OG_HOME__', publicLocale === 'ru' ? 'home-ru.png' : 'home.png')
      .replaceAll('__POSTBRIDGE_OPERATOR_HTML__', publicOperatorHtml)
  },
}

export default defineConfig({
  base,
  plugins: [publicBaseUrlPlugin, react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/react') || id.includes('/node_modules/react-dom') || id.includes('/node_modules/react-router-dom')) {
            return 'vendor'
          }
          return undefined
        },
      },
    },
  },
  server: {
    host: true,
    proxy: {
      '/me': { target: apiProxyTarget, changeOrigin: true },
      '/api': { target: apiProxyTarget, changeOrigin: true },
      '/auth': { target: apiProxyTarget, changeOrigin: true },
      '/assistant': { target: apiProxyTarget, changeOrigin: true },
      '/video-attribution': { target: apiProxyTarget, changeOrigin: true },
      '/billing': { target: apiProxyTarget, changeOrigin: true },
      '/admin': { target: apiProxyTarget, changeOrigin: true },
      '/workspaces': {
        target: apiProxyTarget,
        changeOrigin: true,
        // SSE (AI generate-stream): keep long-running dev-proxy streams open.
        timeout: 0,
        proxyTimeout: 0,
        // Browser refreshes request /workspaces/... directly; serve the SPA shell.
        bypass(req) {
          if (req.headers['x-requested-with'] !== 'XMLHttpRequest' && !req.headers.accept?.includes('application/json')) {
            return '/index.html';
          }
        },
      },
    },
  },
})
