import { fetchAppJson } from './runtime'

export function getVersionCheck() {
  return fetchAppJson('/version-check')
}
