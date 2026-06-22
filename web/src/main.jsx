import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'
import { initMetrika } from './metrika'
import { I18nProvider } from './i18n'
import { applyStoredTheme } from './adapters/theme'

const routerBase = import.meta.env.BASE_URL && import.meta.env.BASE_URL !== '/'
  ? import.meta.env.BASE_URL.replace(/\/$/, '')
  : undefined

initMetrika()
applyStoredTheme()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter basename={routerBase}>
      <I18nProvider>
        <App />
      </I18nProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
