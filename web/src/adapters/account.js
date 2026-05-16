import { api } from './apiClient'

export function updateCurrentUser(payload) {
  return api('/me', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}
