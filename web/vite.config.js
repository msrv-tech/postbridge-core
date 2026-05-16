import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In Docker (environments/dev): VITE_API_PROXY_TARGET=http://saas-api:8001.
// Local host-dev SaaS API listens on :8001 by default.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8001'
const base = process.env.VITE_BASE_PATH || '/'

export default defineConfig({
  base,
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
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
      '/admin': { target: apiProxyTarget, changeOrigin: true },
      '/workspaces': {
        target: apiProxyTarget,
        changeOrigin: true,
        // SSE (ИИ generate-stream): иначе dev-proxy обрывает долгий поток
        timeout: 0,
        proxyTimeout: 0,
        // При обновлении страницы браузер запрашивает /workspaces/... — не проксировать, отдать index.html
        bypass(req) {
          if (req.headers['x-requested-with'] !== 'XMLHttpRequest' && !req.headers.accept?.includes('application/json')) {
            return '/index.html';
          }
        },
      },
    },
  },
})
