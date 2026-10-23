type JsonRecord = Record<string, unknown>

export type NormalizationResult = {
  value: unknown
  warnings: string[]
}

const DATA_PILL_PATTERN = /\{\{\s*([^}]+?)\s*\}\}/g
const DATA_PILL_TEST_PATTERN = /\{\{\s*([^}]+?)\s*\}\}/
const EXACT_DATA_PILL_PATTERN = /^\s*\{\{\s*([^}]+?)\s*\}\}\s*$/

const ACTION_TYPE_ALIASES: Record<string, string> = {
  log: 'core.log',
  core_log: 'core.log',
  sendemail: 'core.sendEmail',
  send_email: 'core.sendEmail',
  email: 'core.sendEmail',
  core_sendemail: 'core.sendEmail',
  core_send_email: 'core.sendEmail',
  updaterecord: 'core.updateRecord',
  update_record: 'core.updateRecord',
  core_updaterecord: 'core.updateRecord',
  core_update_record: 'core.updateRecord',
  lookuprecord: 'core.lookUpRecord',
  lookup_record: 'core.lookUpRecord',
  look_up_record: 'core.lookUpRecord',
  core_lookuprecord: 'core.lookUpRecord',
  core_lookup_record: 'core.lookUpRecord',
  core_look_up_record: 'core.lookUpRecord',
  lookupattachment: 'core.lookupAttachment',
  lookup_attachment: 'core.lookupAttachment',
  look_up_attachment: 'core.lookupAttachment',
  core_lookupattachment: 'core.lookupAttachment',
  core_lookup_attachment: 'core.lookupAttachment',
  core_look_up_attachment: 'core.lookupAttachment',
  'core.lookUpAttachment': 'core.lookupAttachment',
}

const TRIGGER_TYPE_ALIASES: Record<string, string> = {
  created: 'record.created',
  create: 'record.created',
  record_created: 'record.created',
  record_create: 'record.created',
  updated: 'record.updated',
  update: 'record.updated',
  record_updated: 'record.updated',
  record_update: 'record.updated',
  created_updated: 'record.created_or_updated',
  created_or_updated: 'record.created_or_updated',
  createdorupdated: 'record.created_or_updated',
  record_created_updated: 'record.created_or_updated',
  record_created_or_updated: 'record.created_or_updated',
  record_createdorupdated: 'record.created_or_updated',
}

const ACTION_METADATA_KEYS = new Set(['id', 'type', 'order', 'name', 'description', 'inputs'])

const ACTION_ID_PREFIX: Record<string, string> = {
  'core.log': 'log',
  'core.sendEmail': 'send_email',
  'core.updateRecord': 'update_record',
  'core.lookUpRecord': 'look_up_record',
}

export function normalizeDeployRequestInput(input: unknown): NormalizationResult {
  const warnings: string[] = []

  if (!isRecord(input)) {
    return { value: input, warnings }
  }

  return {
    value: {
      ...input,
      flowSpec: normalizeFlowSpecInput(input.flowSpec, warnings),
    },
    warnings,
  }
}

export function normalizeGenerateFlowSourceRequestInput(input: unknown): NormalizationResult {
  const warnings: string[] = []

  if (!isRecord(input)) {
    return { value: input, warnings }
  }

  return {
    value: {
      ...input,
      flowSpec: normalizeFlowSpecInput(input.flowSpec, warnings),
    },
    warnings,
  }
}

export function normalizeFlowSpecInput(input: unknown, warnings: string[] = []): unknown {
  if (!isRecord(input)) {
    return input
  }

  const trigger = normalizeTrigger(input.trigger, warnings)
  const triggerTable = isRecord(trigger) && typeof trigger.table === 'string' ? trigger.table : undefined

  return {
    ...input,
    trigger,
    actions: Array.isArray(input.actions)
      ? input.actions.map((action, index) => normalizeAction(action, index, triggerTable, warnings))
      : input.actions,
  }
}

export function normalizeDeploySubflowRequestInput(input: unknown): NormalizationResult {
  const warnings: string[] = []

  if (!isRecord(input)) {
    return { value: input, warnings }
  }

  return {
    value: {
      ...input,
      subflowSpec: normalizeSubflowSpecInput(input.subflowSpec, warnings),
    },
    warnings,
  }
}

export function normalizeGenerateSubflowSourceRequestInput(input: unknown): NormalizationResult {
  const warnings: string[] = []

  if (!isRecord(input)) {
    return { value: input, warnings }
  }

  return {
    value: {
      ...input,
      subflowSpec: normalizeSubflowSpecInput(input.subflowSpec, warnings),
    },
    warnings,
  }
}

export function normalizeSubflowSpecInput(input: unknown, warnings: string[] = []): unknown {
  if (!isRecord(input)) {
    return input
  }

  return {
    ...input,
    actions: Array.isArray(input.actions)
      ? input.actions.map((action, index) => normalizeAction(action, index, undefined, warnings))
      : input.actions,
  }
}

function normalizeTrigger(input: unknown, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }

  const normalized = { ...input }
  const type = inferRecordTriggerType(input) ?? normalizeKnownValue(input.type, TRIGGER_TYPE_ALIASES)

  if (type && type !== input.type) {
    normalized.type = type
    const eventSuffix = Array.isArray(input.events)
      ? ` using events ${JSON.stringify(input.events)}`
      : ''
    warnings.push(`trigger.type "${String(input.type)}" was normalized to "${type}"${eventSuffix}.`)
  }

  if (normalized.condition === undefined) {
    mapTriggerConditionAlias(normalized, input, warnings)
  }

  if (typeof normalized.condition === 'string') {
    const condition = normalizeRecordTriggerCondition(normalized.condition)
    if (condition !== normalized.condition) {
      warnings.push(
        `trigger.condition was normalized from a trigger.current expression to encoded query "${condition}".`
      )
      normalized.condition = condition
    }
  }

  return normalized
}

function normalizeAction(
  input: unknown,
  index: number,
  triggerTable: string | undefined,
  warnings: string[]
): unknown {
  if (!isRecord(input)) {
    return input
  }

  const normalized = { ...input }
  let type = normalizeKnownValue(input.type, ACTION_TYPE_ALIASES)
  const inputSource = isRecord(input.inputs) ? input.inputs : inlineActionInputs(input)

  const typeStr = typeof input.type === 'string' ? input.type.trim() : undefined
  if (type === typeStr && typeStr && !typeStr.includes('.')) {
    const camel = typeStr.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase())
    type = `core.${camel}`
  }

  if (type && type !== input.type) {
    normalized.type = type
    warnings.push(`actions[${index}].type "${String(input.type)}" was normalized to "${type}".`)
  }

  const actionType = typeof normalized.type === 'string' ? normalized.type : undefined
  const actionId = typeof normalized.id === 'string' && normalized.id.trim()
    ? normalized.id
    : createDefaultActionId(actionType, index)

  if (actionId !== normalized.id) {
    normalized.id = actionId
    warnings.push(`actions[${index}].id was missing and was set to stable id "${actionId}".`)
  }

  switch (actionType) {
    case 'core.log':
      normalized.inputs = normalizeLogInputs(inputSource, actionId, warnings)
      break
    case 'core.sendEmail':
      normalized.inputs = normalizeSendEmailInputs(inputSource, actionId, triggerTable, warnings)
      break
    case 'core.updateRecord':
      normalized.inputs = normalizeUpdateRecordInputs(inputSource, actionId, triggerTable, warnings)
      break
    case 'core.lookUpRecord':
      normalized.inputs = normalizeLookUpRecordInputs(inputSource, actionId, warnings)
      break
    case 'core.sendSms':
      normalized.inputs = normalizeSendSmsInputs(inputSource, actionId, warnings)
      break
    case 'core.associateRecordToEmail':
      normalized.inputs = normalizeAssociateRecordToEmailInputs(inputSource, actionId, warnings)
      break
    case 'core.copyAttachment':
      normalized.inputs = normalizeCopyAttachmentInputs(inputSource, actionId, warnings)
      break
    case 'core.moveAttachment':
      normalized.inputs = normalizeMoveAttachmentInputs(inputSource, actionId, warnings)
      break
    case 'core.addWorknoteLinkToContext':
      normalized.inputs = normalizeAddWorknoteLinkToContextInputs(inputSource, actionId, warnings)
      break
    case 'core.slaPercentageTimer':
      normalized.inputs = normalizeSlaPercentageTimerInputs(inputSource, actionId, warnings)
      break
    case 'core.waitForCondition':
      normalized.inputs = normalizeWaitForConditionInputs(inputSource, actionId, warnings)
      break
    case 'core.lookupAttachment':
      normalized.inputs = normalizeLookUpAttachmentInputs(inputSource, actionId, warnings)
      break
    case 'core.waitForMessage':
      normalized.inputs = normalizeWaitForMessageInputs(inputSource, actionId, warnings)
      break
    case 'core.getAttachmentsOnRecord':
      normalized.inputs = normalizeGetAttachmentsOnRecordInputs(inputSource, actionId, warnings)
      break
    case 'core.lookUpRecords':
      normalized.inputs = normalizeLookUpRecordsInputs(inputSource, actionId, warnings)
      break
    case 'core.createTask':
      normalized.inputs = normalizeCreateTaskInputs(inputSource, actionId, warnings)
      break
    case 'core.createCatalogTask':
      normalized.inputs = normalizeCreateCatalogTaskInputs(inputSource, actionId, warnings)
      break
    case 'core.recordProducer':
      normalized.inputs = normalizeRecordProducerInputs(inputSource, actionId, warnings)
      break
    case 'core.getCatalogVariables':
      normalized.inputs = normalizeGetCatalogVariablesInputs(inputSource, actionId, warnings)
      break
    default:
      if (isRecord(input.inputs)) {
        normalized.inputs = normalizeTemplatesInValue(input.inputs, `actions[${index}].inputs`, warnings)
      }
      break
  }

  return normalized
}

function normalizeLogInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }

  const normalized = { ...input }
  mapAlias(normalized, input, 'log_level', ['level', 'severity'], actionId, warnings)
  mapAlias(normalized, input, 'log_message', ['message', 'text', 'logMessage'], actionId, warnings)

  if (typeof normalized.log_level === 'string') {
    const lower = normalized.log_level.toLowerCase()
    if (lower !== normalized.log_level) {
      normalized.log_level = lower
      warnings.push(`Action "${actionId}": inputs.log_level was converted to lowercase "${lower}".`)
    }
  }

  if (normalized.log_message !== undefined) {
    normalized.log_message = normalizeTemplatesInValue(
      normalized.log_message,
      `action "${actionId}" inputs.log_message`,
      warnings
    )
  }

  return normalized
}

function normalizeSendEmailInputs(
  input: unknown,
  actionId: string,
  triggerTable: string | undefined,
  warnings: string[]
): unknown {
  if (!isRecord(input)) {
    return input
  }

  const normalized = { ...input }
  mapAlias(normalized, input, 'table_name', ['table', 'tableName'], actionId, warnings)
  mapAlias(normalized, input, 'ah_to', ['to', 'recipient', 'recipients', 'email_to', 'emailTo'], actionId, warnings)
  mapAlias(normalized, input, 'ah_cc', ['cc'], actionId, warnings)
  mapAlias(normalized, input, 'ah_bcc', ['bcc'], actionId, warnings)
  mapAlias(normalized, input, 'ah_subject', ['subject'], actionId, warnings)
  mapAlias(normalized, input, 'ah_body', ['body', 'html_body', 'htmlBody', 'message'], actionId, warnings)

  if (normalized.table_name === undefined && triggerTable) {
    normalized.table_name = triggerTable
    warnings.push(`Action "${actionId}": inputs.table_name was defaulted from trigger.table "${triggerTable}".`)
  }

  if (normalized.record === undefined) {
    normalized.record = '{{trigger.current.sys_id}}'
    warnings.push(`Action "${actionId}": inputs.record was defaulted to "{{trigger.current.sys_id}}".`)
  }

  if (normalized.watermark_email === undefined) {
    normalized.watermark_email = true
  } else if (typeof normalized.watermark_email !== 'boolean') {
    const isFalse = String(normalized.watermark_email).trim().toLowerCase() === 'false'
    normalized.watermark_email = !isFalse
    warnings.push(`Action "${actionId}": inputs.watermark_email was converted to boolean ${!isFalse}.`)
  }

  for (const key of ['ah_to', 'ah_cc', 'ah_bcc'] as const) {
    if (Array.isArray(normalized[key])) {
      normalized[key] = normalized[key].join(',')
      warnings.push(`Action "${actionId}": inputs.${key} array was converted to a comma-separated string.`)
    }
  }

  for (const key of ['ah_to', 'ah_cc', 'ah_bcc', 'ah_subject'] as const) {
    if (normalized[key] !== undefined) {
      normalized[key] = normalizeTemplatesInValue(normalized[key], `action "${actionId}" inputs.${key}`, warnings)
    }
  }

  if (normalized.ah_body !== undefined) {
    normalized.ah_body = normalizeStaticEmailBody(normalized.ah_body, actionId, warnings)
  }

  return normalized
}

function normalizeUpdateRecordInputs(
  input: unknown,
  actionId: string,
  triggerTable: string | undefined,
  warnings: string[]
): unknown {
  if (!isRecord(input)) {
    return input
  }

  const normalized = { ...input }
  mapAlias(normalized, input, 'table', ['table_name', 'tableName'], actionId, warnings)
  mapAlias(normalized, input, 'record', ['sys_id', 'target_record', 'targetRecord'], actionId, warnings)
  mapAlias(normalized, input, 'fields', ['values', 'field_values', 'fieldValues'], actionId, warnings)

  if (normalized.table === undefined && triggerTable) {
    normalized.table = triggerTable
    warnings.push(`Action "${actionId}": inputs.table was defaulted from trigger.table "${triggerTable}".`)
  }

  if (normalized.record === undefined) {
    normalized.record = '{{trigger.current.sys_id}}'
    warnings.push(`Action "${actionId}": inputs.record was defaulted to "{{trigger.current.sys_id}}".`)
  }

  if (normalized.fields !== undefined) {
    normalized.fields = normalizeTemplatesInValue(normalized.fields, `action "${actionId}" inputs.fields`, warnings)
  }

  return normalized
}

function normalizeLookUpRecordInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }

  const normalized = { ...input }
  mapAlias(normalized, input, 'table', ['table_name', 'tableName'], actionId, warnings)
  mapAlias(normalized, input, 'encoded_query', ['query', 'encodedQuery'], actionId, warnings)
  mapAlias(normalized, input, 'conditions', ['condition'], actionId, warnings)

  normalizeEncodedQueryObject(normalized, 'encoded_query', actionId, warnings)
  normalizeEncodedQueryObject(normalized, 'conditions', actionId, warnings)

  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeStaticEmailBody(value: unknown, actionId: string, warnings: string[]): unknown {
  const text = normalizeDataPillShorthandInText(
    extractTemplateText(value),
    `action "${actionId}" inputs.ah_body`,
    warnings
  )

  if (!text || !containsDataPillText(text)) {
    return value
  }

  warnings.push(
    `Action "${actionId}": inputs.ah_body contained data pills. ServiceNow SDK does not allow data pills in sendEmail ah_body, so they were converted to static placeholders.`
  )

  return text.replace(DATA_PILL_PATTERN, (_match, path: string) => `[${path.trim()}]`)
}

function normalizeTemplatesInValue(value: unknown, path: string, warnings: string[]): unknown {
  if (typeof value === 'string') {
    const normalizedValue = normalizeDataPillShorthandInText(value, path, warnings) ?? value

    if (containsDataPillText(normalizedValue) && !EXACT_DATA_PILL_PATTERN.test(normalizedValue)) {
      warnings.push(`${path} was wrapped as a template so mixed text and data pills are rendered correctly.`)
      return { template: normalizedValue }
    }

    return normalizedValue
  }

  if (Array.isArray(value)) {
    return value.map((item, index) => normalizeTemplatesInValue(item, `${path}[${index}]`, warnings))
  }

  if (!isRecord(value)) {
    return value
  }

  if (typeof value.template === 'string') {
    return value
  }

  return Object.fromEntries(
    Object.entries(value).map(([key, childValue]) => [
      key,
      normalizeTemplatesInValue(childValue, `${path}.${key}`, warnings),
    ])
  )
}

function mapAlias(
  target: JsonRecord,
  source: JsonRecord,
  targetKey: string,
  aliases: string[],
  actionId: string,
  warnings: string[]
): void {
  if (target[targetKey] !== undefined) {
    return
  }

  for (const alias of aliases) {
    if (source[alias] !== undefined) {
      target[targetKey] = source[alias]
      warnings.push(`Action "${actionId}": inputs.${alias} was mapped to inputs.${targetKey}.`)
      return
    }
  }
}

function normalizeKnownValue(value: unknown, aliases: Record<string, string>): string | undefined {
  if (typeof value !== 'string') {
    return undefined
  }

  return aliases[lookupKey(value)] ?? value.trim()
}

function normalizeRecordTriggerCondition(condition: string): string {
  const humanReadableMatch = condition.match(
    /^\s*([A-Za-z0-9_]+)\s+(equals|is|is not|not equals|does not equal|!=|=)\s+(.+?)\s*$/i
  )

  if (humanReadableMatch) {
    const [, field, rawOperator, rawValue] = humanReadableMatch
    const operator = ['is not', 'not equals', 'does not equal', '!='].includes(rawOperator.toLowerCase())
      ? '!='
      : '='
    return `${field}${operator}${stripWrappingQuotes(rawValue.trim())}`
  }

  const match = condition.match(
    /^\s*(?:\{\{\s*)?trigger\.(?:current\.)?([A-Za-z0-9_]+)(?:\s*\}\})?\s*(==|=|!=|>=|<=|>|<)\s*(.+?)\s*$/
  )

  if (!match) {
    return condition
  }

  const [, field, rawOperator, rawValue] = match
  const operator = rawOperator === '==' ? '=' : rawOperator
  const value = stripWrappingQuotes(rawValue.trim())
  return `${field}${operator}${value}`
}

function normalizeEncodedQueryObject(
  target: JsonRecord,
  key: 'encoded_query' | 'conditions',
  actionId: string,
  warnings: string[]
): void {
  const value = target[key]

  if (!isRecord(value)) {
    return
  }

  target[key] = Object.entries(value)
    .map(([field, fieldValue]) => `${field}=${encodeEncodedQueryValue(fieldValue)}`)
    .join('^')

  warnings.push(`Action "${actionId}": inputs.${key} object was converted to encoded query string.`)
}

function encodeEncodedQueryValue(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  if (value === null) {
    return ''
  }

  return JSON.stringify(value)
}

function inferRecordTriggerType(input: JsonRecord): string | undefined {
  if (lookupKey(String(input.type ?? '')) !== 'record_trigger') {
    return undefined
  }

  const events = Array.isArray(input.events)
    ? new Set(
        input.events
          .filter((event): event is string => typeof event === 'string')
          .map((event) => lookupKey(event))
      )
    : new Set<string>()

  const hasCreated = events.has('created') || events.has('create')
  const hasUpdated = events.has('updated') || events.has('update')

  if (hasCreated && hasUpdated) {
    return 'record.created_or_updated'
  }

  if (hasCreated) {
    return 'record.created'
  }

  if (hasUpdated) {
    return 'record.updated'
  }

  return undefined
}

function mapTriggerConditionAlias(
  target: JsonRecord,
  source: JsonRecord,
  warnings: string[]
): void {
  for (const alias of ['filter', 'query', 'encoded_query', 'encodedQuery']) {
    if (source[alias] !== undefined) {
      target.condition = source[alias]
      warnings.push(`trigger.${alias} was mapped to trigger.condition.`)
      return
    }
  }
}

function inlineActionInputs(input: JsonRecord): unknown {
  const entries = Object.entries(input).filter(([key]) => !ACTION_METADATA_KEYS.has(key))

  if (entries.length === 0) {
    return input.inputs
  }

  return Object.fromEntries(entries)
}

function normalizeDataPillShorthandInText(
  value: string | undefined,
  path: string,
  warnings: string[]
): string | undefined {
  if (!value || !containsDataPillText(value)) {
    return value
  }

  let changed = false
  const normalized = value.replace(DATA_PILL_PATTERN, (match, rawPath: string) => {
    const pillPath = rawPath.trim()
    const normalizedPath = normalizeDataPillPath(pillPath)

    if (normalizedPath === pillPath) {
      return match
    }

    changed = true
    return `{{${normalizedPath}}}`
  })

  if (changed) {
    warnings.push(`${path} trigger data pills were normalized to trigger.current.* form.`)
  }

  return normalized
}

function normalizeDataPillPath(path: string): string {
  if (
    path.startsWith('trigger.') &&
    path !== 'trigger.current' &&
    path !== 'trigger.previous' &&
    !path.startsWith('trigger.current.') &&
    !path.startsWith('trigger.previous.')
  ) {
    return `trigger.current.${path.slice('trigger.'.length)}`
  }

  const actionRecordMatch = path.match(/^action\.([^.]+)\.(?:records|Records)\[(?:0|first)\]\.(.+)$/)
  if (actionRecordMatch) {
    return `action.${actionRecordMatch[1]}.Record.${actionRecordMatch[2]}`
  }

  const actionRecordDotMatch = path.match(/^action\.([^.]+)\.(?:records|Records)\.(?:0|first)\.(.+)$/)
  if (actionRecordDotMatch) {
    return `action.${actionRecordDotMatch[1]}.Record.${actionRecordDotMatch[2]}`
  }

  const actionLowerRecordMatch = path.match(/^action\.([^.]+)\.record\.(.+)$/)
  if (actionLowerRecordMatch) {
    return `action.${actionLowerRecordMatch[1]}.Record.${actionLowerRecordMatch[2]}`
  }

  return path
}

function createDefaultActionId(actionType: string | undefined, index: number): string {
  const prefix = actionType ? ACTION_ID_PREFIX[actionType] ?? lookupKey(actionType) : 'action'
  return `${prefix}_${index + 1}`
}

function lookupKey(value: string): string {
  return value
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

function containsDataPillText(value: string): boolean {
  return DATA_PILL_TEST_PATTERN.test(value)
}

function extractTemplateText(value: unknown): string | undefined {
  if (typeof value === 'string') {
    return value
  }

  if (isRecord(value) && typeof value.template === 'string') {
    return value.template
  }

  return undefined
}

function stripWrappingQuotes(value: string): string {
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1)
  }

  return value
}

function isRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function normalizeSendSmsInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'recipients', ['to', 'recipient', 'sms_to', 'smsTo'], actionId, warnings)
  
  if (Array.isArray(normalized.recipients)) {
    normalized.recipients = normalized.recipients.join(',')
    warnings.push(`Action "${actionId}": inputs.recipients array was converted to a comma-separated string.`)
  }

  delete normalized.to
  delete normalized.recipient
  delete normalized.sms_to
  delete normalized.smsTo
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeAssociateRecordToEmailInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'email_record', ['email', 'emailRecord'], actionId, warnings)
  mapAlias(normalized, input, 'target_record', ['targetRecord'], actionId, warnings)
  delete normalized.email
  delete normalized.emailRecord
  delete normalized.targetRecord
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeCopyAttachmentInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'attachment_record', ['source_record', 'attachment', 'attachmentRecord'], actionId, warnings)
  mapAlias(normalized, input, 'table', ['target_table', 'targetTable', 'table_name', 'tableName'], actionId, warnings)
  mapAlias(normalized, input, 'target_record', ['targetRecord'], actionId, warnings)
  delete normalized.source_record
  delete normalized.source_table
  delete normalized.attachment
  delete normalized.attachmentRecord
  delete normalized.target_table
  delete normalized.targetTable
  delete normalized.table_name
  delete normalized.tableName
  delete normalized.targetRecord
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeMoveAttachmentInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'source_attachment_record', ['source_record', 'attachment', 'attachmentRecord', 'sourceAttachmentRecord'], actionId, warnings)
  mapAlias(normalized, input, 'table', ['target_table', 'targetTable', 'table_name', 'tableName'], actionId, warnings)
  mapAlias(normalized, input, 'target_record', ['targetRecord'], actionId, warnings)
  delete normalized.source_record
  delete normalized.source_table
  delete normalized.attachment
  delete normalized.attachmentRecord
  delete normalized.target_table
  delete normalized.targetTable
  delete normalized.table_name
  delete normalized.tableName
  delete normalized.targetRecord
  delete normalized.sourceAttachmentRecord
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeAddWorknoteLinkToContextInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  if (normalized.journal_field === undefined) {
    normalized.journal_field = 'work_notes'
    warnings.push(`Action "${actionId}": inputs.journal_field was defaulted to "work_notes".`)
  }
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeSlaPercentageTimerInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'task_sla_record', ['sla_record', 'slaRecord', 'taskSlaRecord'], actionId, warnings)
  delete normalized.sla_record
  delete normalized.slaRecord
  delete normalized.taskSlaRecord
  
  if (normalized.percentage !== undefined) {
    const num = Number(normalized.percentage)
    if (!isNaN(num)) {
      normalized.percentage = num
    }
  }
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeWaitForConditionInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'table_name', ['table', 'tableName'], actionId, warnings)
  delete normalized.table
  delete normalized.tableName
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeLookUpAttachmentInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'source_record', ['record', 'sourceRecord'], actionId, warnings)
  delete normalized.record
  delete normalized.sourceRecord
  delete normalized.table
  delete normalized.tableName
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeWaitForMessageInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'message', ['record'], actionId, warnings)
  delete normalized.record
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeGetAttachmentsOnRecordInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'source_record', ['record', 'sourceRecord'], actionId, warnings)
  delete normalized.record
  delete normalized.sourceRecord
  delete normalized.table
  delete normalized.tableName
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeLookUpRecordsInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'table', ['table_name', 'tableName'], actionId, warnings)
  mapAlias(normalized, input, 'conditions', ['query', 'encodedQuery', 'encoded_query', 'condition'], actionId, warnings)
  
  normalizeEncodedQueryObject(normalized, 'conditions', actionId, warnings)
  
  delete normalized.table_name
  delete normalized.tableName
  delete normalized.query
  delete normalized.encodedQuery
  delete normalized.encoded_query
  delete normalized.condition

  if (normalized.max_results !== undefined) {
    const num = Number(normalized.max_results)
    if (!isNaN(num)) {
      normalized.max_results = num
    }
  }
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeCreateTaskInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'task_table', ['table', 'table_name', 'tableName'], actionId, warnings)
  delete normalized.table
  delete normalized.table_name
  delete normalized.tableName
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeCreateCatalogTaskInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'ah_requested_item', ['request_item', 'requested_item'], actionId, warnings)
  delete normalized.request_item
  delete normalized.requested_item
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeRecordProducerInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'catalog_item', ['record_producer'], actionId, warnings)
  delete normalized.record_producer
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}

function normalizeGetCatalogVariablesInputs(input: unknown, actionId: string, warnings: string[]): unknown {
  if (!isRecord(input)) {
    return input
  }
  const normalized = { ...input }
  mapAlias(normalized, input, 'requested_item', ['submitted_request'], actionId, warnings)
  delete normalized.submitted_request
  return normalizeTemplatesInValue(normalized, `action "${actionId}" inputs`, warnings)
}
