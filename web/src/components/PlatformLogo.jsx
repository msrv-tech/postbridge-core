import LocalBrandImage from './LocalBrandImage'
import { platformArtworkId, platformLogo } from '../platformPresentation'
import './PlatformLogo.css'

export default function PlatformLogo({ platform, compact = false }) {
  const logo = platformLogo(platform)
  return <span className={`platform-logo${compact ? ' platform-logo-compact' : ''}`} aria-hidden="true" data-platform-id={platformArtworkId(platform)}>
    <LocalBrandImage src={logo?.src} width={compact ? 16 : 32} height={compact ? 16 : 32} />
  </span>
}
