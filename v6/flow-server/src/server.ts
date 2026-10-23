import 'dotenv/config'
import Fastify from 'fastify'
import { ZodError } from 'zod'
import { deployFlow, isDeployInProgress } from './deploy/deployFlow.js'
import { deploySubflow } from './deploy/deploySubflow.js'
import { verifyFlow, verifySubflow } from './deploy/verifyFlow.js'
import { listAvailableApps } from './deploy/availableApps.js'
import { listAvailableFlows } from './deploy/availableFlows.js'
import { generateFlowSource } from './generator/generateFlowSource.js'
import { generateSubflowSource } from './generator/generateSubflowSource.js'
import {
  availableAppsRequestSchema,
  availableFlowsRequestSchema,
  deployRequestSchema,
  generateFlowSourceRequestSchema,
  deploySubflowRequestSchema,
  generateSubflowSourceRequestSchema,
  verifyFlowRequestSchema,
  verifySubflowRequestSchema,
} from './schema/deployRequest.schema.js'
import {
  normalizeDeployRequestInput,
  normalizeDeploySubflowRequestInput,
  normalizeGenerateFlowSourceRequestInput,
  normalizeGenerateSubflowSourceRequestInput,
  formatZodIssue,
  sanitizeForDebug,
  type NormalizationResult,
} from './validation/index.js'

const server = Fastify({
  logger: {
    redact: ['req.body.password', 'password'],
  },
  bodyLimit: Number(process.env.REQUEST_BODY_LIMIT_BYTES ?? 256 * 1024),
})

function logNormalization(
  request: { log: { info: (...args: unknown[]) => void } },
  normalization: NormalizationResult
): void {
  if (normalization.warnings.length === 0) {
    return
  }

  request.log.info(
    {
      warnings: normalization.warnings,
      normalizedBody: sanitizeForDebug(normalization.value),
    },
    'Request body normalized'
  )
}

server.setErrorHandler((error, request, reply) => {
  const errorName = error instanceof Error ? error.name : 'UnknownError'
  const errorMessage = error instanceof Error ? error.message : 'Unknown server error'

  if (error instanceof ZodError) {
    const issues = error.issues.map((issue) => formatZodIssue(issue, request.body))
    const receivedBody = sanitizeForDebug(request.body)
    request.log.warn({ issues, receivedBody }, 'Invalid request body')

    return reply.status(400).send({
      status: 'error',
      message: 'Invalid request body',
      issues,
      receivedBody,
    })
  }

  request.log.error(error)

  if (errorName === 'DeployConflictError') {
    return reply.status(409).send({
      status: 'error',
      message: errorMessage,
    })
  }

  if (errorName === 'UnsupportedFlowSpecError') {
    return reply.status(400).send({
      status: 'error',
      message: errorMessage,
    })
  }

  return reply.status(500).send({
    status: 'error',
    message: errorMessage,
  })
})

server.get('/health', async () => ({
  status: 'ok',
  deployInProgress: isDeployInProgress(),
}))

server.get('/available-actions', async () => {
  const { availableActions } = await import('./generator/availableActions.js')
  return {
    status: 'ok',
    actions: availableActions,
  }
})

server.post('/generate-flow-source', async (request, reply) => {
  const normalization = normalizeGenerateFlowSourceRequestInput(request.body)
  logNormalization(request, normalization)
  const body = generateFlowSourceRequestSchema.parse(normalization.value)

  return reply.send({
    status: 'generated',
    normalization: {
      applied: normalization.warnings.length > 0,
      warnings: normalization.warnings,
    },
    source: generateFlowSource(body.flowSpec),
  })
})

server.post('/generate-subflow-source', async (request, reply) => {
  const normalization = normalizeGenerateSubflowSourceRequestInput(request.body)
  logNormalization(request, normalization)
  const body = generateSubflowSourceRequestSchema.parse(normalization.value)

  return reply.send({
    status: 'generated',
    normalization: {
      applied: normalization.warnings.length > 0,
      warnings: normalization.warnings,
    },
    source: generateSubflowSource(body.subflowSpec),
  })
})

server.post('/verify-flow', async (request, reply) => {
  const body = verifyFlowRequestSchema.parse(request.body)
  const verification = await verifyFlow(body)

  if (!verification) {
    return reply.status(404).send({
      status: 'not_found',
      flow: null,
    })
  }

  return reply.send({
    status: 'found',
    ...verification,
  })
})

server.post('/verify-subflow', async (request, reply) => {
  const body = verifySubflowRequestSchema.parse(request.body)
  const verification = await verifySubflow(body)

  if (!verification) {
    return reply.status(404).send({
      status: 'not_found',
      flow: null,
    })
  }

  return reply.send({
    status: 'found',
    ...verification,
  })
})

server.post('/available-apps', async (request, reply) => {
  const body = availableAppsRequestSchema.parse(request.body)
  const apps = await listAvailableApps(body)

  return reply.send({
    status: 'ok',
    apps,
  })
})

server.post('/available-flows', async (request, reply) => {
  const body = availableFlowsRequestSchema.parse(request.body)
  const flows = await listAvailableFlows(body)

  return reply.send({
    status: 'ok',
    flows,
  })
})

server.post('/deploy-flow', async (request, reply) => {
  const normalization = normalizeDeployRequestInput(request.body)
  logNormalization(request, normalization)
  const body = deployRequestSchema.parse(normalization.value)
  const result = await deployFlow(body, request.log)
  const httpStatus = result.status === 'installed' || result.status === 'verification_failed' ? 200 : 500

  return reply.status(httpStatus).send({
    status: result.status,
    normalization: {
      applied: normalization.warnings.length > 0,
      warnings: normalization.warnings,
    },
    sys_id: result.verification?.sys_id ?? null,
    name: result.verification?.name ?? body.flowSpec.name,
    active: result.verification?.active ?? null,
    created_by: result.verification?.created_by ?? null,
    updated_on: result.verification?.updated_on ?? null,
    flowDesignerUrl: result.verification?.flowDesignerUrl ?? null,
    warning: result.warning ?? null,
    activation: result.activation ?? null,
    suggestions:
      result.status === 'verification_failed'
        ? {
            message:
              'SDK install completed, but the flow was not found by name. Choose one of these app/scope values and retry if the request targeted the wrong app.',
            requestedScope: result.diagnostics.scope ?? null,
            installedAppSysId: result.diagnostics.installedAppSysId ?? null,
            availableApps: result.diagnostics.availableApps ?? [],
            availableFlows: result.diagnostics.availableFlows ?? [],
          }
        : null,
    diagnostics: result.diagnostics,
    logs: {
      build: {
        command: result.build.command,
        exitCode: result.build.exitCode,
        stdout: result.build.stdout,
        stderr: result.build.stderr,
      },
      install: result.install
        ? {
            command: result.install.command,
            exitCode: result.install.exitCode,
            stdout: result.install.stdout,
            stderr: result.install.stderr,
          }
        : null,
    },
  })
})

server.post('/deploy-subflow', async (request, reply) => {
  const normalization = normalizeDeploySubflowRequestInput(request.body)
  logNormalization(request, normalization)
  const body = deploySubflowRequestSchema.parse(normalization.value)
  const result = await deploySubflow(body, request.log)
  const httpStatus = result.status === 'installed' || result.status === 'verification_failed' ? 200 : 500

  return reply.status(httpStatus).send({
    status: result.status,
    normalization: {
      applied: normalization.warnings.length > 0,
      warnings: normalization.warnings,
    },
    sys_id: result.verification?.sys_id ?? null,
    name: result.verification?.name ?? body.subflowSpec.name,
    active: result.verification?.active ?? null,
    created_by: result.verification?.created_by ?? null,
    updated_on: result.verification?.updated_on ?? null,
    flowDesignerUrl: result.verification?.flowDesignerUrl ?? null,
    warning: result.warning ?? null,
    activation: result.activation ?? null,
    suggestions:
      result.status === 'verification_failed'
        ? {
            message:
              'SDK install completed, but the subflow was not found by name. Choose one of these app/scope values and retry if the request targeted the wrong app.',
            requestedScope: result.diagnostics.scope ?? null,
            installedAppSysId: result.diagnostics.installedAppSysId ?? null,
            availableApps: result.diagnostics.availableApps ?? [],
            availableFlows: result.diagnostics.availableFlows ?? [],
          }
        : null,
    diagnostics: result.diagnostics,
    logs: {
      build: {
        command: result.build.command,
        exitCode: result.build.exitCode,
        stdout: result.build.stdout,
        stderr: result.build.stderr,
      },
      install: result.install
        ? {
            command: result.install.command,
            exitCode: result.install.exitCode,
            stdout: result.install.stdout,
            stderr: result.install.stderr,
          }
        : null,
    },
  })
})

const host = process.env.HOST ?? '127.0.0.1'
const port = Number(process.env.PORT ?? 3000)

server.listen({ host, port }).catch((error) => {
  server.log.error(error)
  process.exit(1)
})
