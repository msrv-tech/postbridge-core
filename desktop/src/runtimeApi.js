import { invoke } from '@tauri-apps/api/core'

export function getRuntimeStatus() {
  return invoke('runtime_status')
}

export function getRuntimeLogs() {
  return invoke('runtime_logs')
}

export function startRuntime() {
  return invoke('runtime_start')
}

export function stopRuntime() {
  return invoke('runtime_stop')
}

export function openPostbridge() {
  return invoke('runtime_open_postbridge')
}
