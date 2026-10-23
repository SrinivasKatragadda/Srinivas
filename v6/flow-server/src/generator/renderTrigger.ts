import type { TriggerSpec } from '../schema/flowSpec.schema.js'
import { toSafeNowId } from './ids.js'
import { tsString } from './sanitize.js'

export function renderTrigger(trigger: TriggerSpec, options: { flowId?: string } = {}): string {
  const triggerId = toSafeNowId(`${options.flowId ?? `${trigger.type}_${trigger.table}`}_trigger`)

  switch (trigger.type) {
    case 'record.created':
      return `trigger.record.created,
    { $id: Now.ID[${tsString(triggerId)}] },
    {
      table: ${tsString(trigger.table)},
      condition: ${tsString(trigger.condition ?? '')},
      run_flow_in: 'background',
      run_on_extended: 'false',
      run_when_setting: 'both',
      run_when_user_setting: 'any',
      run_when_user_list: []
    }`
    case 'record.updated':
      return `trigger.record.updated,
    { $id: Now.ID[${tsString(triggerId)}] },
    {
      table: ${tsString(trigger.table)},
      condition: ${tsString(trigger.condition ?? '')},
      run_flow_in: 'background',
      trigger_strategy: 'unique_changes',
      run_on_extended: 'false',
      run_when_setting: 'both',
      run_when_user_setting: 'any',
      run_when_user_list: []
    }`
    case 'record.created_or_updated':
      return `trigger.record.createdOrUpdated,
    { $id: Now.ID[${tsString(triggerId)}] },
    {
      table: ${tsString(trigger.table)},
      condition: ${tsString(trigger.condition ?? '')},
      run_flow_in: 'background',
      run_on_extended: 'false',
      run_when_setting: 'both',
      run_when_user_setting: 'any',
      run_when_user_list: []
    }`
    default:
      return assertNever(trigger)
  }
}

function assertNever(value: never): never {
  throw new Error(`Unsupported trigger: ${JSON.stringify(value)}`)
}
