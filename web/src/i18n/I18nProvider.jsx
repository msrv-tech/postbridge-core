import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { fetchRuntimeConfig } from '../adapters/runtime'
import { catalogs, DEFAULT_LOCALE, supportedLocales } from './catalogs'

const STORAGE_KEY = 'postbridge.locale'
const supportedLocaleCodes = supportedLocales.map((locale) => locale.code)

const I18nContext = createContext(null)

function normalizeLocale(value) {
  if (!value) return null
  const cleaned = String(value).trim().replace('_', '-').toLowerCase()
  if (!cleaned) return null
  if (supportedLocaleCodes.includes(cleaned)) return cleaned
  const primary = cleaned.split('-')[0]
  return supportedLocaleCodes.includes(primary) ? primary : null
}

function browserLocale() {
  if (typeof navigator === 'undefined') return null
  const languages = Array.isArray(navigator.languages) ? navigator.languages : []
  for (const language of [...languages, navigator.language]) {
    const normalized = normalizeLocale(language)
    if (normalized) return normalized
  }
  return null
}

function initialLocale() {
  if (typeof window === 'undefined') return DEFAULT_LOCALE
  return normalizeLocale(window.localStorage.getItem(STORAGE_KEY)) || browserLocale() || DEFAULT_LOCALE
}

function interpolate(template, params) {
  if (!params) return template
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key) => {
    const value = params[key]
    return value == null ? match : String(value)
  })
}

export function I18nProvider({ children }) {
  const [lockedLocale, setLockedLocale] = useState(null)
  const [isRuntimeConfigLoaded, setRuntimeConfigLoaded] = useState(false)
  const [locale, setLocaleState] = useState(initialLocale)
  const isLocaleLocked = Boolean(lockedLocale)

  useEffect(() => {
    let cancelled = false
    fetchRuntimeConfig()
      .then((config) => {
        if (cancelled) return
        const runtimeLocale = normalizeLocale(config?.i18n?.default_locale || config?.default_locale)
        if ((config?.i18n?.locale_locked || config?.locale_locked) && runtimeLocale) {
          setLockedLocale(runtimeLocale)
          setLocaleState(runtimeLocale)
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setRuntimeConfigLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    document.documentElement.lang = locale
    window.localStorage.setItem(STORAGE_KEY, locale)
  }, [locale])

  const setLocale = useCallback((nextLocale) => {
    if (lockedLocale) return
    const normalized = normalizeLocale(nextLocale)
    if (normalized) setLocaleState(normalized)
  }, [lockedLocale])

  const t = useCallback(
    (key, params) => {
      const value = catalogs[locale]?.[key] ?? catalogs[DEFAULT_LOCALE]?.[key] ?? key
      if (value === key && params?.defaultValue != null) return String(params.defaultValue)
      return interpolate(value, params)
    },
    [locale],
  )

  const value = useMemo(
    () => ({ locale, setLocale, supportedLocales, t, isLocaleLocked, isRuntimeConfigLoaded }),
    [isLocaleLocked, isRuntimeConfigLoaded, locale, setLocale, t],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n() {
  const value = useContext(I18nContext)
  if (!value) throw new Error('useI18n must be used inside I18nProvider')
  return value
}
