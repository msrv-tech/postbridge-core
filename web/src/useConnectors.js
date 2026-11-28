import { useEffect, useState } from 'react'
import { listConnectors } from './adapters/externalApps'
import { projectConnectorCatalog } from './adapters/connectorPolicy'
import { projectCommercePilots } from './adapters/commercePilots'
import { isSelfhostMode } from './adapters/runtime'

export default function useConnectors(workspaceId = '', actorId = '') {
  const disabled = isSelfhostMode()
  const [request, setRequest] = useState({ workspaceId: '', loading: !disabled, items: [], error: null })
  useEffect(() => {
    if (disabled) return undefined
    let active = true
    setRequest({ workspaceId, actorId, loading: true, items: [], error: null })
    const load = listConnectors(workspaceId)
    load.then(payload => {
      if (!active) return
      const items = projectConnectorCatalog(payload)
      // An older API is not evidence of customer readiness. Fail closed.
      if (payload?.items?.length && !items.length) throw new Error('CONNECTOR_PROJECTION_UNAVAILABLE')
      setRequest({ workspaceId, actorId, loading: false, items, error: null,
        pilots: actorId ? projectCommercePilots(payload, workspaceId) : [] })
    }).catch(error => {
      if (active) setRequest({ workspaceId, actorId, loading: false, items: [], error })
    })
    return () => { active = false }
  }, [workspaceId, actorId, disabled])
  if (disabled) return { disabled: true, loading: false, items: [], error: null }
  if (request.workspaceId !== workspaceId || (request.actorId || '') !== actorId) return { disabled: false, loading: true, items: [], error: null }
  return { ...request, disabled: false }
}
