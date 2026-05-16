import { useI18n } from './I18nProvider'

export default function LanguageSelect({ compact = false }) {
  const { isLocaleLocked, isRuntimeConfigLoaded, locale, setLocale, supportedLocales, t } = useI18n()

  if (!isRuntimeConfigLoaded || isLocaleLocked || supportedLocales.length <= 1) return null

  return (
    <label className={compact ? 'language-select language-select-compact' : 'language-select'}>
      <span>{t('i18n.language')}</span>
      <select value={locale} onChange={(event) => setLocale(event.target.value)}>
        {supportedLocales.map((item) => (
          <option key={item.code} value={item.code}>
            {compact ? item.shortLabel : item.label}
          </option>
        ))}
      </select>
    </label>
  )
}
