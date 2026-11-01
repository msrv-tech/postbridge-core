import { useEffect, useId, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useI18n } from '../i18n'

export default function HeaderNavigation({ children, navClassName, label }) {
  const { t } = useI18n()
  const location = useLocation()
  const id = useId()
  const rootRef = useRef(null)
  const toggleRef = useRef(null)
  const [open, setOpen] = useState(false)

  useEffect(() => { setOpen(false) }, [location.pathname, location.search])

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 961px)')
    const closeOnDesktop = () => { if (desktop.matches) setOpen(false) }
    desktop.addEventListener('change', closeOnDesktop)
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [])

  useEffect(() => {
    if (!open) return undefined
    const outside = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false)
    }
    const escape = (event) => {
      if (event.key === 'Escape') {
        setOpen(false)
        toggleRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div className={`header-navigation${open ? ' is-open' : ''}`} ref={rootRef}>
      <button type="button" className="header-menu-toggle" ref={toggleRef}
        aria-label={t(open ? 'navigation.close' : 'navigation.open')}
        aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          {open ? <path d="m6 6 12 12M6 18 18 6" /> : <path d="M4 6h16M4 12h16M4 18h16" />}
        </svg>
      </button>
      <nav id={id} className={`${navClassName} header-menu-panel`} aria-label={label}
        onClick={event => { if (event.target.closest('a[href]')) setOpen(false) }}>
        {children}
      </nav>
    </div>
  )
}
