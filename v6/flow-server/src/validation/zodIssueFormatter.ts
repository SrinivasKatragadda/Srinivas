import type { ZodIssue } from 'zod'

export type FormattedZodIssue = {
  path: string
  message: string
  received: unknown
  allowed?: string[]
}

export function formatZodIssue(issue: ZodIssue, body: unknown): FormattedZodIssue {
  const path = issue.path.join('.')
  const received = previewValue(readPath(body, issue.path))

  if (issue.code === 'invalid_union_discriminator') {
    return {
      path,
      message: issue.message,
      received,
      allowed: issue.options.map((option) => String(option)),
    }
  }

  return {
    path,
    message: issue.message,
    received,
  }
}

function readPath(source: unknown, path: ZodIssue['path']): unknown {
  return path.reduce<unknown>((current, segment) => {
    if (current === null || current === undefined) {
      return undefined
    }

    if (typeof segment === 'number' && Array.isArray(current)) {
      return current[segment]
    }

    if (typeof segment === 'string' && typeof current === 'object' && segment in current) {
      return (current as Record<string, unknown>)[segment]
    }

    return undefined
  }, source)
}

function previewValue(value: unknown): unknown {
  if (value === undefined) {
    return '<missing>'
  }

  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) {
    return value
  }

  if (Array.isArray(value)) {
    return `<array:${value.length}>`
  }

  return '<object>'
}
