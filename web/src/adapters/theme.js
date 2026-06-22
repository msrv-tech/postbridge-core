export const THEME_STORAGE_KEY = 'postbridge.theme'
export const THEMES = {
  dark: 'dark',
  light: 'light',
}

function normalizeTheme(theme) {
  return theme === THEMES.light ? THEMES.light : THEMES.dark
}

export function getStoredTheme() {
  if (typeof window === 'undefined') return THEMES.dark
  try {
    return normalizeTheme(window.localStorage.getItem(THEME_STORAGE_KEY))
  } catch {
    return THEMES.dark
  }
}

export function applyTheme(theme) {
  const normalizedTheme = normalizeTheme(theme)
  if (typeof document === 'undefined') return normalizedTheme
  document.documentElement.dataset.theme = normalizedTheme
  document.documentElement.style.colorScheme = normalizedTheme
  return normalizedTheme
}

export function setStoredTheme(theme) {
  const normalizedTheme = applyTheme(theme)
  if (typeof window === 'undefined') return normalizedTheme
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, normalizedTheme)
  } catch {
    // The visual preference can still be applied for the current page view.
  }
  window.dispatchEvent(new CustomEvent('postbridge:themechange', { detail: { theme: normalizedTheme } }))
  return normalizedTheme
}

export function applyStoredTheme() {
  return applyTheme(getStoredTheme())
}
