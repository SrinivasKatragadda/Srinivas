import type { AvailableFlowsRequest } from '../schema/deployRequest.schema.js'
import { assertAllowedInstanceUrl } from './verifyFlow.js'

export type AvailableFlow = {
  sys_id: string
  name: string
  active: string | boolean
  sys_scope: string
  created_by: string
  updated_on: string
}

type FlowResponse = {
  result?: Array<{
    sys_id?: string
    name?: string
    active?: string | boolean
    sys_scope?: string
    sys_created_by?: string
    sys_updated_on?: string
  }>
}

export async function listAvailableFlows(input: AvailableFlowsRequest): Promise<AvailableFlow[]> {
  await assertAllowedInstanceUrl(input.instanceUrl)

  const queries: string[] = []
  if (input.scopeId) {
    queries.push(`sys_scope=${input.scopeId}^ORDERBYDESCsys_updated_on`)
  }
  if (input.flowName) {
    queries.push(`nameLIKE${input.flowName}^ORDERBYDESCsys_updated_on`)
  }
  if (queries.length === 0) {
    queries.push('ORDERBYDESCsys_updated_on')
  }

  const merged = new Map<string, AvailableFlow>()
  for (const encodedQuery of queries) {
    const flows = await queryFlows(input, encodedQuery)
    for (const flow of flows) {
      merged.set(flow.sys_id, flow)
    }
  }

  return [...merged.values()].slice(0, input.limit)
}

async function queryFlows(input: AvailableFlowsRequest, encodedQuery: string): Promise<AvailableFlow[]> {
  const instanceUrl = new URL(input.instanceUrl)
  const query = new URL('/api/now/table/sys_hub_flow', instanceUrl)
  query.searchParams.set('sysparm_query', encodedQuery)
  query.searchParams.set('sysparm_fields', 'sys_id,name,active,sys_scope,sys_created_by,sys_updated_on')
  query.searchParams.set('sysparm_limit', String(input.limit))

  const response = await fetch(query, {
    headers: {
      Accept: 'application/json',
      Authorization: `Basic ${Buffer.from(`${input.username}:${input.password}`).toString('base64')}`,
    },
  })

  if (!response.ok) {
    throw new Error(`Unable to list available flows. sys_hub_flow query failed with HTTP ${response.status}.`)
  }

  const body = (await response.json()) as FlowResponse
  return (body.result ?? [])
    .filter((record) => record.sys_id && record.name)
    .map((record) => ({
      sys_id: record.sys_id!,
      name: record.name!,
      active: record.active ?? '',
      sys_scope: record.sys_scope ?? '',
      created_by: record.sys_created_by ?? '',
      updated_on: record.sys_updated_on ?? '',
    }))
}
