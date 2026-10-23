import type { SubflowSpec, SubflowIoSpec } from '../schema/subflowSpec.schema.js'
import { toSafeNowId } from './ids.js'
import { collectActionDataPillReferences, createRenderContext, renderDataPill } from './renderDataPill.js'
import { renderAction } from './renderAction.js'
import { indent, tsString } from './sanitize.js'

export function generateSubflowSource(subflowSpec: SubflowSpec): string {
  const subflowId = toSafeNowId(subflowSpec.id)
  validateUniqueActionVariables(subflowSpec)

  const context = createRenderContext('_params', '_params.inputs')
  const referencedActionIds = collectReferencedActionIds(subflowSpec)
  const renderedActions = subflowSpec.actions.map(
    (action) =>
      renderAction(action, context, {
        assignToVariable: referencedActionIds.has(action.id) || referencedActionIds.has(toSafeNowId(action.id)),
      }).source
  )
  const actionBody = renderedActions.map((source) => indent(source, 4)).join('\n\n')

  const usedColumns = new Set<string>()

  const inputsMap = Object.entries(subflowSpec.inputs)
    .map(([key, spec]) => {
      const { source, columnFn } = renderColumn(spec)
      usedColumns.add(columnFn)
      return `${key}: ${source}`
    })
    .join(',\n      ')

  const outputsMap = Object.entries(subflowSpec.outputs)
    .map(([key, spec]) => {
      const { source, columnFn } = renderColumn(spec)
      usedColumns.add(columnFn)
      return `${key}: ${source}`
    })
    .join(',\n      ')

  const outputValuesMap = Object.entries(subflowSpec.outputValues ?? {})
    .map(([key, value]) => `${key}: ${renderDataPill(value, context)}`)
    .join(',\n        ')

  const assignOutputsSource = outputValuesMap
    ? `wfa.flowLogic.assignSubflowOutputs(
      { $id: Now.ID[${tsString(`${subflowId}_assign_outputs`)}] },
      _params.outputs,
      {
        ${outputValuesMap}
      }
    )`
    : ''

  const coreImports = usedColumns.size > 0 
    ? `import { ${Array.from(usedColumns).join(', ')} } from '@servicenow/sdk/core'`
    : ''

  const bodyStatements = [actionBody, assignOutputsSource]
    .filter((source) => source.trim().length > 0)
    .map((source) => indent(source, 4))
    .join('\n\n')

  return `import { action, Subflow, wfa } from '@servicenow/sdk/automation'
${coreImports}

Subflow(
  {
    $id: Now.ID[${tsString(subflowId)}],
    name: ${tsString(subflowSpec.name)},
    description: ${tsString(subflowSpec.description ?? '')},
    runAs: ${tsString(subflowSpec.runAs ?? 'system')},
    inputs: {
      ${inputsMap}
    },
    outputs: {
      ${outputsMap}
    }
  },
  (_params) => {
${bodyStatements}
  }
)
`
}

function renderColumn(spec: SubflowIoSpec): { source: string; columnFn: string } {
  let columnFn = 'StringColumn'
  if (spec.type === 'integer') columnFn = 'IntegerColumn'
  if (spec.type === 'boolean') columnFn = 'BooleanColumn'

  const mandatoryStr = spec.mandatory ? ', mandatory: true' : ''
  return {
    source: `${columnFn}({ label: ${tsString(spec.label)}${mandatoryStr} })`,
    columnFn
  }
}

function collectReferencedActionIds(subflowSpec: SubflowSpec): Set<string> {
  const referencedActionIds = new Set<string>()

  for (const action of subflowSpec.actions) {
    collectActionDataPillReferences(action.inputs, referencedActionIds)
  }

  return referencedActionIds
}

function validateUniqueActionVariables(subflowSpec: SubflowSpec): void {
  const seen = new Map<string, string>()

  for (const action of subflowSpec.actions) {
    const safeId = toSafeNowId(action.id)
    const existing = seen.get(safeId)
    if (existing) {
      throw new Error(`Action ids "${existing}" and "${action.id}" both normalize to "${safeId}"`)
    }

    seen.set(safeId, action.id)
  }
}
