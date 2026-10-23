import { createHash } from 'node:crypto'
import type { DeploySubflowRequest } from '../schema/deployRequest.schema.js'
import { generateSubflowSource } from '../generator/generateSubflowSource.js'
import {
  acquireDeployLock,
  activateWorkspace,
  releaseDeployLock,
  writeItemSource,
  type ScopeConfig,
  type WrittenItem,
} from './workspace.js'
import { runSdkCommand, type SdkRunResult } from './sdkRunner.js'
import { assertAllowedInstanceUrl, verifyFlow, type FlowVerification } from './verifyFlow.js'
import { listAvailableApps, type AvailableApp } from './availableApps.js'
import { listAvailableFlows, type AvailableFlow } from './availableFlows.js'

export type DeployFlowResult = {
  status: 'installed' | 'build_failed' | 'install_failed' | 'verification_failed'
  verification: FlowVerification | null
  build: SdkRunResult
  install: SdkRunResult | null
  warning?: string
  activation?: FlowActivationResult
  diagnostics: DeployDiagnostics
}

export type DeployDiagnostics = {
  scope?: ScopeConfig
  fallback?: InstallFallbackDiagnostics
  installedAppSysId?: string
  availableApps?: AvailableApp[]
  availableFlows?: AvailableFlow[]
}

export type DeployLogger = {
  info: (...args: unknown[]) => void
  warn: (...args: unknown[]) => void
  error: (...args: unknown[]) => void
  debug?: (...args: unknown[]) => void
}

export { isDeployInProgress } from './workspace.js'

export async function deploySubflow(request: DeploySubflowRequest, logger?: DeployLogger): Promise<DeployFlowResult> {
  acquireDeployLock()
  const diagnostics: DeployDiagnostics = {}
  let written: WrittenItem | null = null

  try {
    logDeploy(logger, 'info', 'Deploy started', {
      instanceUrl: new URL(request.instanceUrl).origin,
      requestedScopeId: request.scopeId,
      requestedScopeName: request.scopeName,
      reinstall: request.reinstall,
      flowId: request.subflowSpec.id,
      flowName: request.subflowSpec.name,
    })

    await assertAllowedInstanceUrl(request.instanceUrl)
    const { scopeConfig, item } = await writeSdkProjectFiles(request)
    written = item
    diagnostics.scope = scopeConfig

    logDeploy(logger, 'info', 'SDK project files generated', {
      scope: scopeConfig.scope,
      scopeId: scopeConfig.scopeId,
      name: scopeConfig.name,
      flowName: request.subflowSpec.name,
    })

    const credentials = {
      instanceUrl: request.instanceUrl,
      username: request.username,
      password: request.password,
    }

    logDeploy(logger, 'info', 'SDK build started')
    const build = await runSdkCommand(['@servicenow/sdk', 'build'], credentials)
    logDeploy(logger, build.exitCode === 0 ? 'info' : 'error', 'SDK build finished', {
      exitCode: build.exitCode,
      output: summarizeSdkOutput(build),
    })
    if (build.exitCode !== 0) {
      await written?.revert()
      return {
        status: 'build_failed',
        verification: null,
        build,
        install: null,
        diagnostics,
      }
    }

    logDeploy(logger, 'info', 'SDK install started')
    const installArgs = [
      '@servicenow/sdk',
      'install',
      '--reinstall',
      request.reinstall ? 'true' : 'false',
      '--open-browser',
      'false',
    ]
    const install = await runSdkCommand(installArgs, credentials)
    logDeploy(logger, install.exitCode === 0 ? 'info' : 'warn', 'SDK install finished', {
      exitCode: install.exitCode,
      output: summarizeSdkOutput(install),
    })

    if (install.exitCode !== 0) {
      const fallback = isSdkInstallStatusCheckFailure(install) && !request.skipInstallFallback
        ? await waitForVerifiedFlow(
            request,
            Number(process.env.INSTALL_VERIFY_TIMEOUT_MS ?? 15_000),
            logger
          )
        : {
            verification: null,
            diagnostics: {
              triggered: false,
              reason: request.skipInstallFallback
                ? 'Install fallback verification skipped by request.'
                : 'SDK install failed for a reason other than install status detection.',
              timeoutMs: 0,
              attempts: 0,
              found: false,
              startedAt: new Date().toISOString(),
              completedAt: new Date().toISOString(),
            },
          }

      diagnostics.fallback = fallback.diagnostics

      if (fallback.verification) {
        const activation = await activateFlows(request, scopeConfig.scopeId)
        logDeploy(logger, activation.ok ? 'info' : 'warn', 'Flow activation finished', activation)
        const postActivationVerification = await verifyFlow({
          instanceUrl: request.instanceUrl,
          username: request.username,
          password: request.password,
          flowName: request.subflowSpec.name,
        })

        return {
          status: 'installed',
          verification: postActivationVerification ?? fallback.verification,
          build,
          install,
          warning:
            'SDK install returned a status-detection failure, but the flow was found on the instance after install.',
          activation,
          diagnostics,
        }
      }

      logDeploy(logger, 'warn', 'SDK install failed and fallback verification did not find the flow', {
        fallback: diagnostics.fallback,
      })

      await written?.revert()
      return {
        status: 'install_failed',
        verification: null,
        build,
        install,
        diagnostics,
      }
    }

    diagnostics.installedAppSysId = extractInstalledAppSysId(install)

    let verification = await verifyFlow({
      instanceUrl: request.instanceUrl,
      username: request.username,
      password: request.password,
      flowName: request.subflowSpec.name,
    })
    let activation: FlowActivationResult | undefined
    if (verification && String(verification.active) !== 'true') {
      activation = await activateFlows(request, scopeConfig.scopeId)
      logDeploy(logger, activation.ok ? 'info' : 'warn', 'Activation after install finished', activation)
      verification =
        (await verifyFlow({
          instanceUrl: request.instanceUrl,
          username: request.username,
          password: request.password,
          flowName: request.subflowSpec.name,
        })) ?? verification
    }

    if (!verification) {
      diagnostics.availableApps = await listAvailableApps({
        instanceUrl: request.instanceUrl,
        username: request.username,
        password: request.password,
        limit: 25,
      }).catch((error) => {
        logDeploy(logger, 'warn', 'Unable to fetch available app suggestions', {
          error: error instanceof Error ? error.message : String(error),
        })
        return []
      })

      diagnostics.availableFlows = await listAvailableFlows({
        instanceUrl: request.instanceUrl,
        username: request.username,
        password: request.password,
        flowName: request.subflowSpec.name,
        scopeId: diagnostics.installedAppSysId ?? scopeConfig.scopeId,
        limit: 25,
      }).catch((error) => {
        logDeploy(logger, 'warn', 'Unable to fetch available flow suggestions', {
          error: error instanceof Error ? error.message : String(error),
        })
        return []
      })

      logDeploy(logger, 'warn', 'Install succeeded but flow verification did not find the flow', {
        flowName: request.subflowSpec.name,
        installedAppSysId: diagnostics.installedAppSysId,
        availableAppCount: diagnostics.availableApps.length,
        availableFlowCount: diagnostics.availableFlows.length,
      })
    }

    return {
      status: verification ? 'installed' : 'verification_failed',
      verification,
      build,
      install,
      activation,
      diagnostics,
    }
  } finally {
    logDeploy(logger, 'info', 'Deploy finished', { flowName: request.subflowSpec.name })
    releaseDeployLock()
  }
}

async function writeSdkProjectFiles(
  request: DeploySubflowRequest
): Promise<{ scopeConfig: ScopeConfig; item: WrittenItem }> {
  const scopeConfig = await resolveScopeConfig(request)
  await activateWorkspace(scopeConfig)
  const item = await writeItemSource('subflow', request.subflowSpec.id, generateSubflowSource(request.subflowSpec))
  return { scopeConfig, item }
}


async function resolveScopeConfig(request: DeploySubflowRequest): Promise<ScopeConfig> {
  if (isGlobalScopeRequest(request)) {
    const name = request.scopeName.trim().toLowerCase() === 'global' ? `${request.subflowSpec.name} App` : request.scopeName
    const scopeId = /^[0-9a-f]{32}$/i.test(request.scopeId)
      ? request.scopeId.toLowerCase()
      : deterministicGlobalAppId(request.subflowSpec.id, name)

    return {
      scope: 'global',
      name,
      scopeId,
    }
  }

  const bySysId = /^[0-9a-f]{32}$/i.test(request.scopeId)
  const query = bySysId ? `sys_id=${request.scopeId.toLowerCase()}` : `scope=${request.scopeId}`
  const scopeRecord = await fetchScopeRecord(request, query)

  if (!scopeRecord) {
    const expected = bySysId ? `sys_id "${request.scopeId}"` : `technical scope "${request.scopeId}"`
    throw new Error(
      `Unable to resolve ServiceNow application scope for ${expected}. The SDK requires now.config.json scopeId to be the 32-character sys_scope sys_id. Pass an existing technical scope such as "x_hmz_flowauto" or the actual 32-character scope sys_id.`
    )
  }

  return {
    scope: scopeRecord.scope,
    name: request.scopeName || scopeRecord.name,
    scopeId: scopeRecord.sys_id,
  }
}

function isGlobalScopeRequest(request: DeploySubflowRequest): boolean {
  return request.scopeId.trim().toLowerCase() === 'global' || request.scopeName.trim().toLowerCase() === 'global'
}

function deterministicGlobalAppId(flowId: string, appName: string): string {
  return createHash('sha256').update(`global-app:${appName}:${flowId}`).digest('hex').slice(0, 32)
}

type ScopeRecord = {
  sys_id: string
  scope: string
  name: string
}

type ScopeResponse = {
  result?: Array<Partial<ScopeRecord>>
}

async function fetchScopeRecord(request: DeploySubflowRequest, encodedQuery: string): Promise<ScopeRecord | null> {
  const instanceUrl = new URL(request.instanceUrl)
  const query = new URL('/api/now/table/sys_scope', instanceUrl)
  query.searchParams.set('sysparm_query', encodedQuery)
  query.searchParams.set('sysparm_fields', 'sys_id,scope,name')
  query.searchParams.set('sysparm_limit', '1')

  const response = await fetch(query, {
    headers: {
      Accept: 'application/json',
      Authorization: `Basic ${Buffer.from(`${request.username}:${request.password}`).toString('base64')}`,
    },
  })

  if (!response.ok) {
    throw new Error(`Scope lookup failed with HTTP ${response.status}. Confirm the user can read sys_scope.`)
  }

  const body = (await response.json()) as ScopeResponse
  const record = body.result?.[0]

  if (!record?.sys_id || !record.scope || !record.name) {
    return null
  }

  return {
    sys_id: record.sys_id,
    scope: record.scope,
    name: record.name,
  }
}

function isSdkInstallStatusCheckFailure(install: SdkRunResult): boolean {
  return `${install.stdout}\n${install.stderr}`.includes('Could not determine app installation status')
}

type InstallFallbackDiagnostics = {
  triggered: boolean
  reason: string
  timeoutMs: number
  attempts: number
  found: boolean
  startedAt: string
  completedAt: string
  lastError?: string
}

type InstallFallbackResult = {
  verification: FlowVerification | null
  diagnostics: InstallFallbackDiagnostics
}

async function waitForVerifiedFlow(
  request: DeploySubflowRequest,
  timeoutMs: number,
  logger?: DeployLogger
): Promise<InstallFallbackResult> {
  const deadline = Date.now() + timeoutMs
  const diagnostics: InstallFallbackDiagnostics = {
    triggered: true,
    reason: 'SDK install could not determine app installation status.',
    timeoutMs,
    attempts: 0,
    found: false,
    startedAt: new Date().toISOString(),
    completedAt: '',
  }

  logDeploy(logger, 'warn', 'Install fallback verification started', {
    flowName: request.subflowSpec.name,
    timeoutMs,
  })

  while (Date.now() < deadline) {
    diagnostics.attempts += 1
    try {
      const verification = await verifyFlow({
        instanceUrl: request.instanceUrl,
        username: request.username,
        password: request.password,
        flowName: request.subflowSpec.name,
      })

      if (verification) {
        diagnostics.found = true
        diagnostics.completedAt = new Date().toISOString()
        logDeploy(logger, 'info', 'Install fallback found flow', {
          attempts: diagnostics.attempts,
          sys_id: verification.sys_id,
          active: verification.active,
        })
        return {
          verification,
          diagnostics,
        }
      }

      logDeploy(logger, 'info', 'Install fallback did not find flow yet', {
        attempt: diagnostics.attempts,
        flowName: request.subflowSpec.name,
      })
    } catch (error) {
      diagnostics.lastError = error instanceof Error ? error.message : String(error)
      logDeploy(logger, 'warn', 'Install fallback verification attempt failed', {
        attempt: diagnostics.attempts,
        error: diagnostics.lastError,
      })
    }

    await delay(Number(process.env.INSTALL_VERIFY_POLL_INTERVAL_MS ?? 3_000))
  }

  diagnostics.completedAt = new Date().toISOString()
  logDeploy(logger, 'warn', 'Install fallback verification timed out', diagnostics)

  return {
    verification: null,
    diagnostics,
  }
}

type FlowActivationResult = {
  attempted: boolean
  ok: boolean
  message: string
}

type FlowActivationResponse = {
  result?: {
    summary?: {
      total?: number
      succeeded?: number
      failed?: number
    }
  }
  error?: {
    message?: string
  }
  message?: string
}

async function activateFlows(request: DeploySubflowRequest, scopeSysId: string): Promise<FlowActivationResult> {
  const instanceUrl = new URL(request.instanceUrl)
  const activateUrl = new URL('/api/now/wfa_fluent/activate_flows', instanceUrl)
  activateUrl.searchParams.set('sysparm_transaction_scope', scopeSysId)

  const response = await fetch(activateUrl, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      Authorization: `Basic ${Buffer.from(`${request.username}:${request.password}`).toString('base64')}`,
    },
  })

  const body = (await response.json().catch(() => null)) as FlowActivationResponse | null
  const summary = body?.result?.summary
  const message =
    summary
      ? `Flow activation complete: ${summary.succeeded ?? 0}/${summary.total ?? 0} succeeded, ${summary.failed ?? 0} failed`
      : body?.error?.message ?? body?.message ?? response.statusText

  if (response.ok || response.status === 422) {
    return {
      attempted: true,
      ok: response.ok && summary?.failed === 0,
      message,
    }
  }

  if (typeof message === 'string' && message.includes('does not represent any resource')) {
    return {
      attempted: true,
      ok: false,
      message: 'Flow activation endpoint not found on the instance.',
    }
  }

  return {
    attempted: true,
    ok: false,
    message,
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function logDeploy(
  logger: DeployLogger | undefined,
  level: 'info' | 'warn' | 'error',
  message: string,
  data: Record<string, unknown> = {}
): void {
  if (!logger) {
    return
  }

  logger[level](data, message)
}

function summarizeSdkOutput(result: SdkRunResult): string {
  return `${result.stdout}\n${result.stderr}`
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(-12)
    .join('\n') || '(no output)'
}

function extractInstalledAppSysId(install: SdkRunResult): string | undefined {
  const match = `${install.stdout}\n${install.stderr}`.match(/sys_app\.do\?sys_id=([0-9a-f]{32})/i)
  return match?.[1]?.toLowerCase()
}
