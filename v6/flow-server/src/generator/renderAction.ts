import type { ActionSpec } from '../schema/flowSpec.schema.js'
import { toSafeNowId, toSafeVariableName } from './ids.js'
import {
  registerActionVariable,
  renderDataPill,
  valueContainsDataPill,
  type RenderContext,
} from './renderDataPill.js'
import { tsString } from './sanitize.js'

const TYPED_CORE_ACTIONS = new Set([
  'addWorknoteLinkToContext',
  'associateRecordToEmail',
  'copyAttachment',
  'fireEvent',
  'getAttachmentsOnRecord',
  'lookUpRecords',
  'lookupAttachment',
  'moveAttachment',
  'sendSms',
  'waitForCondition',
  'waitForEmailReply',
  'waitForMessage',
])

const ALL_CORE_ACTIONS = new Set([
  'addWorknoteLinkToContext',
  'askForApproval',
  'associateRecordToEmail',
  'copyAttachment',
  'createCatalogTask',
  'createOrUpdateRecord',
  'createRecord',
  'createTask',
  'deleteAttachment',
  'deleteRecord',
  'fireEvent',
  'getAttachmentsOnRecord',
  'getCatalogVariables',
  'getEmailHeader',
  'getLatestResponseTextFromEmail',
  'log',
  'lookUpEmailAttachments',
  'lookUpRecord',
  'lookUpRecords',
  'lookupAttachment',
  'moveAttachment',
  'moveEmailAttachmentsToRecord',
  'recordProducer',
  'sendEmail',
  'sendNotification',
  'sendSms',
  'slaPercentageTimer',
  'submitCatalogItemRequest',
  'updateMultipleRecords',
  'updateRecord',
  'waitForCondition',
  'waitForEmailReply',
  'waitForMessage',
])

export type RenderedAction = {
  variableName: string
  source: string
}

export function renderAction(
  actionSpec: ActionSpec,
  context: RenderContext,
  options: { assignToVariable: boolean }
): RenderedAction {
  const variableName = toSafeVariableName(actionSpec.id)
  const actionId = toSafeNowId(actionSpec.id)
  const actionStart = options.assignToVariable ? `const ${variableName} = wfa.action` : 'wfa.action'
  let source: string

  switch (actionSpec.type) {
    case 'subflow.call': {
      // Call a subflow deployed separately, by its sys_id:
      //   wfa.subflow('<sys_id>', { $id }, { <input>: <value>, waitForCompletion })
      const inputs = ((actionSpec as any).inputs ?? {}) as Record<string, unknown>
      const subflowSysId = inputs.subflow_sys_id
      if (typeof subflowSysId !== 'string' || !/^[0-9a-f]{32}$/i.test(subflowSysId)) {
        throw unsupportedFlowSpec(
          `Action "${actionSpec.id}" (subflow.call) needs inputs.subflow_sys_id: the 32-character sys_id of a deployed subflow.`
        )
      }
      const subflowInputs = isRecord(inputs.subflow_inputs) ? inputs.subflow_inputs : {}
      const rendered = Object.entries(subflowInputs).map(
        ([key, value]) => `${tsString(key)}: ${renderDataPill(value as any, context)}`
      )
      rendered.push(`waitForCompletion: ${inputs.wait_for_completion === false ? 'false' : 'true'}`)
      const callStart = options.assignToVariable ? `const ${variableName} = wfa.subflow` : 'wfa.subflow'
      source = `${callStart}(
      ${tsString(subflowSysId.toLowerCase())},
      { $id: Now.ID[${tsString(actionId)}] },
      {
        ${rendered.join(',\n        ')}
      }
    )`
      break
    }
    case 'core.log':
      source = `${actionStart}(
      action.core.log,
      { $id: Now.ID[${tsString(actionId)}] },
      {
        log_level: ${renderDataPill(actionSpec.inputs.log_level, context)},
        log_message: ${renderDataPill(actionSpec.inputs.log_message, context)}
      }
    )`
      break
    case 'core.sendEmail':
      if (valueContainsDataPill(actionSpec.inputs.ah_body)) {
        throw unsupportedFlowSpec(
          `Action "${actionSpec.id}" uses data pills in core.sendEmail ah_body. ServiceNow SDK 4.7 rejects data pills in ah_body; use a static string body, put dynamic values in ah_subject/log_message, or add a custom supported email action renderer.`
        )
      }

      source = `${actionStart}(
      action.core.sendEmail,
      { $id: Now.ID[${tsString(actionId)}] },
      {
        table_name: ${renderDataPill(actionSpec.inputs.table_name, context)},
        record: ${renderDataPill(actionSpec.inputs.record, context)},
        watermark_email: ${renderDataPill(actionSpec.inputs.watermark_email ?? false, context)},
        ah_to: ${renderDataPill(actionSpec.inputs.ah_to, context)},
        ah_cc: ${renderDataPill(actionSpec.inputs.ah_cc ?? '', context)},
        ah_bcc: ${renderDataPill(actionSpec.inputs.ah_bcc ?? '', context)},
        ah_subject: ${renderDataPill(actionSpec.inputs.ah_subject, context)},
        ah_body: ${renderDataPill(actionSpec.inputs.ah_body, context)}
      }
    )`
      break
    case 'core.updateRecord':
      source = `${actionStart}(
      action.core.updateRecord,
      { $id: Now.ID[${tsString(actionId)}] },
      {
        table_name: ${renderDataPill(actionSpec.inputs.table, context)},
        record: ${renderDataPill(actionSpec.inputs.record, context)},
        values: TemplateValue(${renderDataPill(actionSpec.inputs.fields, context)})
      }
    )`
      break
    case 'core.lookUpRecord':
      source = `${actionStart}(
      action.core.lookUpRecord,
      { $id: Now.ID[${tsString(actionId)}] },
      {
        table: ${renderDataPill(actionSpec.inputs.table, context)},
        conditions: ${renderDataPill(actionSpec.inputs.conditions ?? actionSpec.inputs.encoded_query ?? '', context)},
        sort_type: ${renderDataPill(actionSpec.inputs.sort_type ?? 'sort_asc', context)},
        if_multiple_records_are_found_action: ${renderDataPill(actionSpec.inputs.if_multiple_records_are_found_action ?? 'use_first_record', context)}
      }
    )`
      break
    default: {
      const parts = actionSpec.type.split('.')
      if (parts.length !== 2) {
        throw unsupportedFlowSpec(`Unsupported action type format: ${actionSpec.type}`)
      }
      const namespace = parts[0]
      const actionName = parts[1]
      
      const inputsObj = (actionSpec as any).inputs || {}
      const renderedInputs: string[] = []
      
      for (const [key, value] of Object.entries(inputsObj)) {
        renderedInputs.push(`${key}: ${renderActionInput(actionName, key, value, context)}`)
      }
      
      const isTypedCore = namespace === 'core' && TYPED_CORE_ACTIONS.has(actionName)
      const isCore = namespace === 'core' && ALL_CORE_ACTIONS.has(actionName)
      
      let actionRef: string
      if (isTypedCore) {
        actionRef = `action.core.${actionName}`
      } else if (isCore) {
        actionRef = `action.core.${actionName} as any`
      } else if (namespace === 'core') {
        throw unsupportedFlowSpec(
          `Unsupported core action "${actionSpec.type}". It is not exported by the installed ServiceNow SDK action.core built-ins.`
        )
      } else {
        actionRef = tsString(actionSpec.type)
      }

      source = `${actionStart}(
      ${actionRef},
      { $id: Now.ID[${tsString(actionId)}] },
      {
        ${renderedInputs.join(',\n        ')}
      }
    )`
      break
    }
  }

  registerActionVariable(context, actionSpec.id, variableName)
  registerActionVariable(context, actionId, variableName)

  return {
    variableName,
    source,
  }
}

function renderActionInput(
  actionName: string,
  key: string,
  value: unknown,
  context: RenderContext
): string {
  const renderedValue = renderDataPill(value as any, context)

  if (actionName === 'askForApproval' && key === 'approval_conditions' && isRecord(value)) {
    return `wfa.approvalRules(${renderedValue})`
  }

  if (actionName === 'askForApproval' && key === 'due_date' && isRecord(value)) {
    return `wfa.approvalDueDate(${renderedValue})`
  }

  if (needsTemplateValue(actionName, key) && isRecord(value)) {
    return `TemplateValue(${renderedValue})`
  }

  return renderedValue
}

function needsTemplateValue(actionName: string, key: string): boolean {
  return (
    ['createRecord', 'updateRecord', 'createTask', 'updateMultipleRecords', 'createOrUpdateRecord'].includes(
      actionName
    ) && ['values', 'fields', 'field_values'].includes(key)
  ) || (actionName === 'createCatalogTask' && key === 'ah_fields')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function assertNever(value: never): never {
  throw new Error(`Unsupported action: ${JSON.stringify(value)}`)
}

function unsupportedFlowSpec(message: string): Error {
  const error = new Error(message)
  error.name = 'UnsupportedFlowSpecError'
  return error
}
