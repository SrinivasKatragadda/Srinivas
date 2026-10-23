import type { FlowSpec } from '../schema/flowSpec.schema.js'
import { toSafeNowId } from './ids.js'
import { collectActionDataPillReferences, createRenderContext } from './renderDataPill.js'
import { renderAction } from './renderAction.js'
import { renderTrigger } from './renderTrigger.js'
import { indent, tsString } from './sanitize.js'

export function generateFlowSource(flowSpec: FlowSpec): string {
  const flowId = toSafeNowId(flowSpec.id)
  validateUniqueActionVariables(flowSpec)

  const context = createRenderContext()
  const renderedTrigger = renderTrigger(flowSpec.trigger, { flowId })
  const referencedActionIds = collectReferencedActionIds(flowSpec)
  const renderedActions = flowSpec.actions.map(
    (action) =>
      renderAction(action, context, {
        assignToVariable: referencedActionIds.has(action.id) || referencedActionIds.has(toSafeNowId(action.id)),
      }).source
  )
  const actionBody = renderedActions.map((source) => indent(source, 4)).join('\n\n')

  return `import { action, Flow, trigger, wfa } from '@servicenow/sdk/automation'

Flow(
  {
    $id: Now.ID[${tsString(flowId)}],
    name: ${tsString(flowSpec.name)},
    description: ${tsString(flowSpec.description ?? '')},
    runAs: ${tsString(flowSpec.runAs)}
  },
  wfa.trigger(${renderedTrigger}),
  (_params) => {
${actionBody}
  }
)
`
}

function collectReferencedActionIds(flowSpec: FlowSpec): Set<string> {
  const referencedActionIds = new Set<string>()

  for (const action of flowSpec.actions) {
    collectActionDataPillReferences(action.inputs, referencedActionIds)
  }

  return referencedActionIds
}

function validateUniqueActionVariables(flowSpec: FlowSpec): void {
  const seen = new Map<string, string>()

  for (const action of flowSpec.actions) {
    const safeId = toSafeNowId(action.id)
    const existing = seen.get(safeId)
    if (existing) {
      throw new Error(`Action ids "${existing}" and "${action.id}" both normalize to "${safeId}"`)
    }

    seen.set(safeId, action.id)
  }
}
