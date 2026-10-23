const MAX_NOW_ID_LENGTH = 80
const RESERVED_WORDS = new Set([
  'await',
  'break',
  'case',
  'catch',
  'class',
  'const',
  'continue',
  'debugger',
  'default',
  'delete',
  'do',
  'else',
  'enum',
  'export',
  'extends',
  'false',
  'finally',
  'for',
  'function',
  'if',
  'import',
  'in',
  'instanceof',
  'new',
  'null',
  'return',
  'super',
  'switch',
  'this',
  'throw',
  'true',
  'try',
  'typeof',
  'var',
  'void',
  'while',
  'with',
  'yield',
])

export function toSafeNowId(input: string): string {
  const safe = input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_')
    .slice(0, MAX_NOW_ID_LENGTH)

  if (!safe) {
    throw new Error(`Unable to create a safe Now.ID key from "${input}"`)
  }

  if (!/^[a-z]/.test(safe)) {
    return `id_${safe}`.slice(0, MAX_NOW_ID_LENGTH)
  }

  return safe
}

export function toSafeVariableName(input: string): string {
  const safe = toSafeNowId(input)
  const variableName = safe.replace(/_([a-z0-9])/g, (_, char: string) => char.toUpperCase())
  return RESERVED_WORDS.has(variableName) ? `${variableName}Action` : variableName
}
