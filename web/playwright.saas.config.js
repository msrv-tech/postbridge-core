import { defineConfig, devices } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const port = Number(process.env.POSTBRIDGE_WEB_E2E_SAAS_PORT || 4176)
const host = '127.0.0.1'
const webRoot = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: /saas-smoke\.spec\.js/,
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: `http://${host}:${port}`,
    trace: 'on-first-retry',
  },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  webServer: {
    command: `npm run dev -- --host ${host} --port ${port}`,
    cwd: webRoot,
    url: `http://${host}:${port}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      VITE_BASE_PATH: '/',
      VITE_POSTBRIDGE_APP_MODE: 'saas',
    },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
