import type { AvailableAppsRequest } from '../schema/deployRequest.schema.js'
import { assertAllowedInstanceUrl } from './verifyFlow.js'

export type AvailableApp = {
  sys_id: string
  scope: string
  name: string
  active: string | boolean
  sourceTable: 'sys_scope'
}

type ScopeResponse = {
  result?: Array<{
    sys_id?: string
    scope?: string
    name?: string
    active?: string | boolean
  }>
}

export async function listAvailableApps(input: AvailableAppsRequest): Promise<AvailableApp[]> {
  await assertAllowedInstanceUrl(input.instanceUrl)

  const instanceUrl = new URL(input.instanceUrl)
  const query = new URL('/api/now/table/sys_scope', instanceUrl)
  query.searchParams.set('sysparm_query', 'ORDERBYname')
  query.searchParams.set('sysparm_fields', 'sys_id,scope,name,active')
  query.searchParams.set('sysparm_limit', String(input.limit))

  const response = await fetch(query, {
    headers: {
      Accept: 'application/json',
      Authorization: `Basic ${Buffer.from(`${input.username}:${input.password}`).toString('base64')}`,
    },
  })

  if (!response.ok) {
    throw new Error(`Unable to list available ServiceNow apps/scopes. sys_scope query failed with HTTP ${response.status}.`)
  }

  const body = (await response.json()) as ScopeResponse
  return (body.result ?? [])
    .filter((record) => record.sys_id && record.scope && record.name)
    .map((record) => ({
      sys_id: record.sys_id!,
      scope: record.scope!,
      name: record.name!,
      active: record.active ?? '',
      sourceTable: 'sys_scope',
    }))
}
