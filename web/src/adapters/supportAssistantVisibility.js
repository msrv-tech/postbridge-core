export const supportAssistantVisibilityEvent = 'postbridge-support-assistant-visibility'

function visibilityKey(workspaceId) {
  return `postbridge_support_assistant_hidden_${workspaceId}`
}

export function isSupportAssistantHidden(workspaceId) {
  if (typeof window === 'undefined' || !workspaceId) return false
  return window.localStorage.getItem(visibilityKey(workspaceId)) === '1'
}

export function setSupportAssistantHidden(workspaceId, hidden) {
  if (typeof window === 'undefined' || !workspaceId) return
  if (hidden) {
    window.localStorage.setItem(visibilityKey(workspaceId), '1')
  } else {
    window.localStorage.removeItem(visibilityKey(workspaceId))
  }
  window.dispatchEvent(
    new CustomEvent(supportAssistantVisibilityEvent, {
      detail: { workspaceId, hidden: Boolean(hidden) },
    }),
  )
}
