export function tsString(value: string): string {
  return JSON.stringify(value)
}

export function indent(value: string, spaces = 2): string {
  const prefix = ' '.repeat(spaces)
  return value
    .split('\n')
    .map((line) => (line.length > 0 ? `${prefix}${line}` : line))
    .join('\n')
}

export function redactSecret(value: string, secret?: string): string {
  if (!secret) {
    return value
  }

  return value.split(secret).join('[REDACTED]')
}
