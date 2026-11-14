import { useEffect, useState } from 'react'
import { useI18n } from '../i18n'
import PlatformLogo from './PlatformLogo'

const normalizeId = value => String(value || '').trim().toLowerCase().replace(/^twitter$/, 'x')
const disabledPlatforms = new Set(String(import.meta.env.VITE_POSTBRIDGE_DISABLED_PLATFORMS || '').split(',').map(normalizeId).filter(Boolean))
const roleKeys = {
  direct_publication: 'home.platforms.role.direct',
  video_publication: 'home.platforms.role.video',
  rss_import_or_feed: 'home.platforms.role.rss',
  internal_source: 'home.platforms.role.internal',
}

function catalogItems(payload) {
  if (!Array.isArray(payload?.items)) throw new Error('PLATFORM_CATALOG_UNAVAILABLE')
  const seen = new Set()
  return payload.items.flatMap(item => {
    const id = normalizeId(item?.id)
    if (typeof item?.id !== 'string' || !/^[a-z0-9][a-z0-9_-]*$/.test(id) || typeof item?.display_name !== 'string' || !item.display_name.trim()) {
      throw new Error('PLATFORM_CATALOG_UNAVAILABLE')
    }
    if (seen.has(id) || disabledPlatforms.has(id) || (item.status && item.status !== 'available')) return []
    seen.add(id)
    return [{
      id,
      name: item.display_name.trim(),
      roleKey: Object.hasOwn(roleKeys, item.presentation_role) ? roleKeys[item.presentation_role] : 'home.platforms.role.generic',
      detailPath: item.detail_path === `/platforms/${id}` ? item.detail_path : null,
    }]
  })
}

export default function HomePlatforms() {
  const { t } = useI18n()
  const [catalog, setCatalog] = useState({ loading: true, items: [], error: false })
  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/providers', { signal: controller.signal, credentials: 'omit', headers: { Accept: 'application/json' } })
      .then(response => {
        if (!response.ok) throw new Error('PLATFORM_CATALOG_UNAVAILABLE')
        return response.json()
      })
      .then(payload => {
        const items = catalogItems(payload)
        if (!controller.signal.aborted) setCatalog({ loading: false, items, error: false })
      })
      .catch(() => {
        if (!controller.signal.aborted) setCatalog({ loading: false, items: [], error: true })
      })
    return () => controller.abort()
  }, [])

  if (catalog.loading || catalog.error || !catalog.items.length) {
    const key = catalog.loading ? 'common.loading' : catalog.error ? 'home.platforms.unavailable' : 'home.platforms.empty'
    return <p role="status" className="muted">{t(key)}</p>
  }
  return <div className="platform-showcase" aria-label={t('home.platforms.aria')}>
    {catalog.items.map(platform => <article className={`platform-showcase-item platform-showcase-item-${platform.id}`} key={platform.id} data-provider-id={platform.id}>
      <PlatformLogo platform={platform.id} />
      <h3>{platform.detailPath ? <a href={platform.detailPath}>{platform.name}</a> : platform.name}</h3>
      <p className="platform-showcase-role">{t(platform.roleKey)}</p>
    </article>)}
  </div>
}
