import type { FlowInputValue } from '../schema/flowSpec.schema.js'
import { tsString } from './sanitize.js'

const DATA_PILL_PATTERN = /\{\{\s*([^}]+?)\s*\}\}/g
const REFERENCE_FIELD_NAMES = new Set([
  'attachment_record',
  'catalog_item',
  'email_record',
  'requested_item',
  'source_attachment_record',
  'source_record',
  'target_record',
  'task_sla_record',
  'template_catalog_item',
])

export type RenderContext = {
  actionVariables: Map<string, string>
  triggerParameterName: string
  inputParameterName?: string
}

export function createRenderContext(triggerParameterName = '_params', inputParameterName?: string): RenderContext {
  return {
    actionVariables: new Map(),
    triggerParameterName,
    inputParameterName,
  }
}

export function registerActionVariable(context: RenderContext, actionId: string, variableName: string): void {
  context.actionVariables.set(actionId, variableName)
}

export function renderDataPill(value: FlowInputValue, context: RenderContext): string {
  if (typeof value === 'string') {
    return renderStringOrDataPill(value, context)
  }

  if (typeof value === 'number' || typeof value === 'boolean' || value === null) {
    return JSON.stringify(value)
  }

  if (Array.isArray(value)) {
    return `[${value.map((item) => renderDataPill(item, context)).join(', ')}]`
  }

  if ('template' in value && typeof value.template === 'string') {
    return renderTemplate(value.template, context)
  }

  const entries = Object.entries(value).map(([key, item]) => `${tsString(key)}: ${renderDataPill(item, context)}`)
  return `{ ${entries.join(', ')} }`
}

export function valueContainsDataPill(value: FlowInputValue): boolean {
  if (typeof value === 'string') {
    return containsDataPillText(value)
  }

  if (typeof value === 'number' || typeof value === 'boolean' || value === null) {
    return false
  }

  if (Array.isArray(value)) {
    return value.some(valueContainsDataPill)
  }

  if ('template' in value && typeof value.template === 'string') {
    return containsDataPillText(value.template)
  }

  return Object.values(value).some(valueContainsDataPill)
}

export function collectActionDataPillReferences(value: FlowInputValue, references = new Set<string>()): Set<string> {
  if (typeof value === 'string') {
    collectActionReferencesFromText(value, references)
    return references
  }

  if (typeof value === 'number' || typeof value === 'boolean' || value === null) {
    return references
  }

  if (Array.isArray(value)) {
    value.forEach((item) => collectActionDataPillReferences(item, references))
    return references
  }

  if ('template' in value && typeof value.template === 'string') {
    collectActionReferencesFromText(value.template, references)
    return references
  }

  Object.values(value).forEach((item) => collectActionDataPillReferences(item, references))
  return references
}

function renderStringOrDataPill(value: string, context: RenderContext): string {
  const fullMatch = value.match(/^\{\{\s*([^}]+?)\s*\}\}$/)
  if (fullMatch) {
    return renderSingleDataPill(fullMatch[1], context)
  }

  return tsString(value)
}

function collectActionReferencesFromText(value: string, references: Set<string>): void {
  for (const match of value.matchAll(DATA_PILL_PATTERN)) {
    const path = match[1].trim()
    if (!path.startsWith('action.')) {
      continue
    }

    const [, actionId] = path.split('.')
    if (actionId) {
      references.add(actionId)
    }
  }
}

function containsDataPillText(value: string): boolean {
  return /\{\{\s*([^}]+?)\s*\}\}/.test(value)
}

function renderTemplate(template: string, context: RenderContext): string {
  const matches = [...template.matchAll(DATA_PILL_PATTERN)]
  if (matches.length === 0) {
    return tsString(template)
  }

  let cursor = 0
  let output = '`'

  for (const match of matches) {
    const index = match.index ?? 0
    output += escapeTemplateText(template.slice(cursor, index))
    output += '${' + renderSingleDataPill(match[1], context) + '}'
    cursor = index + match[0].length
  }

  output += escapeTemplateText(template.slice(cursor))
  output += '`'
  return output
}

function renderSingleDataPill(path: string, context: RenderContext): string {
  const expression = renderDataPillExpression(path.trim(), context)
  const type = inferDataPillType(path.trim())
  return `wfa.dataPill(${expression}, ${tsString(type)})`
}

function renderDataPillExpression(path: string, context: RenderContext): string {
  if (path.startsWith('trigger.')) {
    // Cast through `any` because the JSON spec can reference arbitrary record
    // fields that may not exist on the SDK's strict TableAwareRecord<T> type.
    return `(${context.triggerParameterName}.trigger as any).${path.slice('trigger.'.length)}`
  }

  if (path.startsWith('action.')) {
    const parts = path.split('.')
    if (parts.length < 3) {
      throw new Error(`Invalid action data pill path "${path}"`)
    }

    const actionId = parts[1]
    const variableName = context.actionVariables.get(actionId)
    if (!variableName) {
      throw new Error(`Action data pill "${path}" references action "${actionId}" before it is rendered`)
    }

    return [variableName, ...parts.slice(2)].join('.')
  }

  if (path.startsWith('input.') || path.startsWith('inputs.')) {
    if (!context.inputParameterName) {
      throw new Error(`Unsupported data pill root in "${path}". Use trigger.* or action.*`)
    }

    const parts = path.split('.')
    if (parts.length !== 2 || !parts[1]) {
      throw new Error(`Invalid subflow input data pill path "${path}"`)
    }

    return `${context.inputParameterName}.${parts[1]}`
  }

  const supportedRoots = context.inputParameterName ? 'input.*, inputs.*, action.*' : 'trigger.* or action.*'
  throw new Error(`Unsupported data pill root in "${path}". Use ${supportedRoots}`)
}

function inferDataPillType(path: string): 'string' | 'reference' | 'boolean' | 'integer' {
  const lastSegment = path.split('.').at(-1)?.toLowerCase()

  if (
    /^action\.[^.]+\.email$/i.test(path) ||
    (path.startsWith('trigger.') && (lastSegment === 'current' || lastSegment === 'previous')) ||
    lastSegment === 'sys_id' ||
    lastSegment === 'record' ||
    lastSegment?.endsWith('_id') ||
    (lastSegment !== undefined && REFERENCE_FIELD_NAMES.has(lastSegment))
  ) {
    return 'reference'
  }

  if (lastSegment?.startsWith('is_') || lastSegment?.startsWith('has_') || lastSegment?.startsWith('can_')) {
    return 'boolean'
  }

  if (lastSegment?.includes('count') || lastSegment?.includes('amount')) {
    return 'integer'
  }

  return 'string'
}

function escapeTemplateText(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/`/g, '\\`').replace(/\$/g, '\\$')
}
