import { lookup } from 'node:dns/promises'
import { isIP } from 'node:net'

export type VerifyFlowInput = {
  instanceUrl: string
  username: string
  password: string
  flowName: string
}

export type FlowVerification = {
  sys_id: string
  name: string
  active: string | boolean
  created_by: string
  updated_on: string
  flowDesignerUrl: string
}

type ServiceNowTableResponse = {
  result?: Array<{
    sys_id?: string
    name?: string
    active?: string | boolean
    sys_created_by?: string
    sys_updated_on?: string
  }>
}

export async function verifyFlow(input: VerifyFlowInput): Promise<FlowVerification | null> {
  return verifyRecord(input, 'flow')
}

export async function verifySubflow(input: VerifyFlowInput): Promise<FlowVerification | null> {
  return verifyRecord(input, 'subflow')
}

async function verifyRecord(input: VerifyFlowInput, type: 'flow' | 'subflow'): Promise<FlowVerification | null> {
  await assertAllowedInstanceUrl(input.instanceUrl)

  const instanceUrl = new URL(input.instanceUrl)
  const query = new URL('/api/now/table/sys_hub_flow', instanceUrl)
  query.searchParams.set('sysparm_query', `name=${input.flowName}`)
  query.searchParams.set('sysparm_fields', 'sys_id,name,active,sys_created_by,sys_updated_on')
  query.searchParams.set('sysparm_limit', '1')

  const response = await fetch(query, {
    headers: {
      Accept: 'application/json',
      Authorization: `Basic ${Buffer.from(`${input.username}:${input.password}`).toString('base64')}`,
    },
  })

  if (!response.ok) {
    throw new Error(`Flow verification failed with HTTP ${response.status}`)
  }

  const body = (await response.json()) as ServiceNowTableResponse
  const record = body.result?.[0]
  if (!record?.sys_id || !record.name) {
    return null
  }

  return {
    sys_id: record.sys_id,
    name: record.name,
    active: record.active ?? '',
    created_by: record.sys_created_by ?? '',
    updated_on: record.sys_updated_on ?? '',
    flowDesignerUrl: new URL(`/now/flow-designer/designer/${type}/${record.sys_id}`, instanceUrl).toString(),
  }
}

export async function assertAllowedInstanceUrl(instanceUrl: string): Promise<void> {
  const url = new URL(instanceUrl)

  if (url.protocol !== 'https:') {
    throw new Error('instanceUrl must use https')
  }

  if (process.env.ALLOW_PRIVATE_INSTANCE_URLS === 'true') {
    return
  }

  const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, '')

  if (hostname === 'localhost' || hostname.endsWith('.localhost')) {
    throw new Error('Private or localhost instance URLs are rejected unless ALLOW_PRIVATE_INSTANCE_URLS=true')
  }

  if (isPrivateHostname(hostname)) {
    throw new Error('Private instance URLs are rejected unless ALLOW_PRIVATE_INSTANCE_URLS=true')
  }

  const addresses = await lookup(hostname, { all: true, verbatim: true })
  for (const address of addresses) {
    if (isPrivateAddress(address.address)) {
      throw new Error('Private instance URL resolved to a private address; set ALLOW_PRIVATE_INSTANCE_URLS=true to allow this')
    }
  }
}

function isPrivateHostname(hostname: string): boolean {
  if (!isIP(hostname)) {
    return false
  }

  return isPrivateAddress(hostname)
}

function isPrivateAddress(address: string): boolean {
  const normalized = address.toLowerCase()

  if (normalized.startsWith('::ffff:')) {
    return isPrivateAddress(normalized.slice('::ffff:'.length))
  }

  if (address === '0.0.0.0' || address === '127.0.0.1' || address === '::' || address === '::1') {
    return true
  }

  if (address.startsWith('10.') || address.startsWith('127.') || address.startsWith('192.168.')) {
    return true
  }

  const octets = address.split('.').map(Number)
  if (octets.length === 4) {
    if (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) {
      return true
    }

    if (octets[0] === 169 && octets[1] === 254) {
      return true
    }

    if (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127) {
      return true
    }
  }

  return normalized.startsWith('fc') || normalized.startsWith('fd') || normalized.startsWith('fe80:')
}
