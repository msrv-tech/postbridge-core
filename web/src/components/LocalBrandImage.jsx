import { useState } from 'react'

// Shared local artwork rendering for platform and connector presentation only.
export default function LocalBrandImage({ src, width = 32, height = 32 }) {
  const [failedSrc, setFailedSrc] = useState(null)
  return src && failedSrc !== src
    ? <img src={src} alt="" aria-hidden="true" draggable="false" width={width} height={height} onError={() => setFailedSrc(src)} />
    : <svg className="local-brand-fallback" style={{ color: '#64748b' }} width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true" focusable="false">
      <circle cx="6" cy="12" r="3" /><circle cx="18" cy="6" r="3" /><circle cx="18" cy="18" r="3" /><path d="m8.7 10.7 6.6-3.4m-6.6 6 6.6 3.4" />
    </svg>
}
