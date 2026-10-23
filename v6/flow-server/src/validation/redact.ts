export function sanitizeForDebug(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeForDebug(item))
  }

  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, childValue]) => [
        key,
        shouldRedactKey(key) ? '<redacted>' : sanitizeForDebug(childValue),
      ])
    )
  }

  return value
}

function shouldRedactKey(key: string): boolean {
  const normalized = key.toLowerCase()
  return normalized.includes('password') || normalized === 'pwd' || normalized.endsWith('_pwd')
}
