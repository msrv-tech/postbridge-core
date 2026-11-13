// Neutral presentation metadata only. Availability and readiness come from the API.
const logoBase = `${import.meta.env.BASE_URL}connector-brands/`
export const connectorBrandArtwork = {
  moysklad: { file: 'moysklad.svg', shape: 'wordmark', guide: 'moysklad' },
  amocrm: { file: 'amocrm.svg', shape: 'wordmark', guide: 'amocrm' },
  bitrix24: { file: 'bitrix24.svg', shape: 'wordmark', guide: 'bitrix24' },
  openai: { file: 'openai.svg', shape: 'openai', guide: 'mcp' },
  ozon: { file: 'ozon.svg', shape: 'wordmark', guide: 'ozon' },
  'yandex-market': { file: 'yandex-market.svg', shape: 'symbol', guide: 'yandex-market' },
  wildberries: { file: 'wildberries.svg', shape: 'symbol', guide: 'wildberries' },
  fresh: { file: 'onec.png', shape: 'company', guide: 'onec-fresh' },
  'yandex-ai': { file: 'yandex-ai.svg', shape: 'symbol', guide: 'yandex-ai' },
}
export const mcpLogoUrl = `${logoBase}mcp.svg`

export function connectorStatusLabel(item, t) {
  return t(item?.rollout === 'disabled' ? 'connectors.status.unavailable' : `connectors.readiness.${item?.public_readiness}`)
}

export function connectorLogo(item) {
  const artwork = connectorBrandArtwork[item?.brand_key]
  return artwork ? { ...artwork, src: `${logoBase}${artwork.file}` } : null
}

export function connectorCopy(item, t) {
  const guide = connectorBrandArtwork[item?.brand_key]?.guide
  const translated = Boolean(guide)
  const prefix = `connectors.guide.${guide}`
  return {
    name: translated ? t(`${prefix}.name`) : item?.display_name || item?.id || '',
    benefit: translated ? t(`${prefix}.benefit`) : item?.summary || '',
    steps: translated && item.setup_mode !== 'none' ? [1, 2, 3].map(step => t(`${prefix}.step.${step}`)) : [],
  }
}
