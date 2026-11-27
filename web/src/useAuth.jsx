import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { getToken, isAuthenticated } from './adapters/sessionToken'
import { captureVideoAttribution, claimVideoAttribution } from './videoAttribution'
import { createWorkspace as postWorkspace, loadCurrentUser } from './adapters/auth'

captureVideoAttribution()
const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const requestRef = useRef(0)
  const refreshUser = useCallback(async () => {
    const request = ++requestRef.current
    if (!isAuthenticated()) {
      setUser(null)
      setLoading(false)
      return null
    }
    const token = getToken()
    let data = null
    try { data = await loadCurrentUser() } catch { /* Signed out or unavailable. */ }
    if (request !== requestRef.current || token !== getToken()) return null
    setUser(data)
    setLoading(false)
    if (data) claimVideoAttribution()
    return data
  }, [])

  const createWorkspace = async (name, requestId) => {
    const token = getToken()
    const workspace = await postWorkspace(name, requestId)
    if (getToken() !== token) return null
    // An earlier profile request may not include the newly committed workspace.
    requestRef.current += 1
    setUser((current) => {
      if (!current) return current
      const workspaces = [...(current.workspaces || []).filter((item) => item.id !== workspace.id), workspace]
      workspaces.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id))
      return { ...current, workspaces }
    })
    setLoading(false)
    return workspace
  }

  useEffect(() => {
    const onAuthChange = () => { setUser(null); setLoading(true); refreshUser() }
    const onStorage = (event) => { if (event.key === 'postbridge_token' || event.key === null) onAuthChange() }
    window.addEventListener('postbridge-auth-changed', onAuthChange)
    window.addEventListener('storage', onStorage)
    refreshUser()
    return () => {
      requestRef.current += 1
      window.removeEventListener('postbridge-auth-changed', onAuthChange)
      window.removeEventListener('storage', onStorage)
    }
  }, [refreshUser])

  return <AuthContext.Provider value={{ user, loading, refreshUser, createWorkspace }}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth requires AuthProvider')
  return context
}
