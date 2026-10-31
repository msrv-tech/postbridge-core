import { preferredWorkspaceId } from '../adapters/workspace'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../useAuth'
import LoadingSkeleton from '../components/LoadingSkeleton'

/** Redirects global /settings to the account’s last selected workspace. */
export default function SettingsRedirect() {
  const { user, loading } = useAuth()
  if (loading) return <LoadingSkeleton />
  const id = preferredWorkspaceId(user)
  if (!id) return <Navigate to="/" replace />
  return <Navigate to={`/workspaces/${id}/settings`} replace />
}
