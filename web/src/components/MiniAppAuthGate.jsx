import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authenticateMiniApp } from '../adapters/authFlows'
import { setToken, isAuthenticated } from '../adapters/sessionToken'
import LoadingSkeleton from './LoadingSkeleton'
import { ensureTelegramWebAppScript, isLikelyTelegramEmbedded } from '../telegramWebAppLoader'

function initialGateState() {
  if (typeof window === 'undefined') return 'checking'
  if (isAuthenticated()) return 'ready'
  if (!isLikelyTelegramEmbedded()) return 'ready'
  return 'checking'
}

/**
 * Mini App (Telegram / MAX): авторизация по initData.
 * SDK Telegram не грузим на всех страницах — только если похоже на встроенный WebView.
 */
export default function MiniAppAuthGate({ children }) {
  const navigate = useNavigate()
  const [state, setState] = useState(initialGateState)

  useEffect(() => {
    if (isAuthenticated()) {
      setState('ready')
      return undefined
    }
    if (!isLikelyTelegramEmbedded()) {
      setState('ready')
      return undefined
    }

    let mounted = true
    const tryAuth = (initData, endpoint) => {
      if (!initData) return false
      setState('authing')
      authenticateMiniApp(endpoint, initData)
        .then((res) => {
          if (!mounted) return
          setToken(res.token)
          setState('ready')
          navigate('/', { replace: true })
        })
        .catch(() => mounted && setState('ready'))
      return true
    }

    const run = async () => {
      try {
        await ensureTelegramWebAppScript()
      } catch {
        if (mounted) setState('ready')
        return
      }
      if (!mounted) return

      if (tryAuth(window.Telegram?.WebApp?.initData, 'telegram')) return
      if (tryAuth(window.WebApp?.initData, 'max')) return

      const deadline = Date.now() + 2500
      const poll = () => {
        if (!mounted || Date.now() > deadline) {
          if (mounted) setState('ready')
          return
        }
        if (tryAuth(window.Telegram?.WebApp?.initData, 'telegram')) return
        if (tryAuth(window.WebApp?.initData, 'max')) return
        setTimeout(poll, 80)
      }
      poll()
    }

    run()
    return () => {
      mounted = false
    }
  }, [navigate])

  if (state === 'authing' || state === 'checking') {
    return <LoadingSkeleton />
  }
  return children
}
