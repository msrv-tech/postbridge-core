import { useState } from 'react'
import { useI18n } from '../i18n'

/** Показ https://t.me/... для копирования и открытия на другом устройстве. */
export default function TelegramDeepLinkField({ url }) {
  const [copied, setCopied] = useState(false)
  const { t } = useI18n()

  const copy = async () => {
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* Clipboard может быть недоступен — пользователь выделит из поля */
    }
  }

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <label
        className="text-small"
        htmlFor="telegram-deep-link-url"
        style={{ display: 'block', marginBottom: '0.35rem', color: '#475569' }}
      >
        {t('telegram.deepLink.label')}
      </label>
      <input
        id="telegram-deep-link-url"
        readOnly
        value={url || ''}
        onFocus={(e) => e.target.select()}
        aria-label={t('telegram.deepLink.aria')}
        style={{
          width: '100%',
          boxSizing: 'border-box',
          padding: '0.55rem 0.65rem',
          fontSize: '0.8rem',
          fontFamily: 'ui-monospace, monospace',
          borderRadius: '6px',
          border: '1px solid #94a3b8',
          background: '#ffffff',
          color: '#0f172a',
          WebkitTextFillColor: '#0f172a',
          marginBottom: '0.5rem',
        }}
      />
      <button
        type="button"
        className="btn btn-secondary"
        style={{ width: '100%' }}
        onClick={copy}
        disabled={!url}
      >
        {copied ? t('telegram.deepLink.copied') : t('telegram.deepLink.copy')}
      </button>
    </div>
  )
}
