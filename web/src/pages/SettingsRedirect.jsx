import { Navigate } from 'react-router-dom'
import { useAuth } from '../useAuth'
import LoadingSkeleton from '../components/LoadingSkeleton'

/** Редирект с глобального /settings на настройки первого workspace. */
export default function SettingsRedirect() {
  const { user, loading } = useAuth()
  if (loading) return <LoadingSkeleton />
  const id = user?.workspaces?.[0]?.id
  if (!id) return <Navigate to="/" replace />
  return <Navigate to={`/workspaces/${id}/settings`} replace />
}
