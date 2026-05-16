export function getCapability(user, name) {
  return user?.capabilities?.[name] || user?.features?.[name] || null
}

export function isCapabilityEnabled(user, name, defaultEnabled = true) {
  const capability = getCapability(user, name)
  return capability?.enabled ?? defaultEnabled
}
