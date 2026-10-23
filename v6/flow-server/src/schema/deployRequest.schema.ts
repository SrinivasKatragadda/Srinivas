import { z } from 'zod'
import { flowSpecSchema } from './flowSpec.schema.js'
import { subflowSpecSchema } from './subflowSpec.schema.js'

const httpsUrlSchema = z
  .string()
  .url()
  .refine((value) => new URL(value).protocol === 'https:', {
    message: 'instanceUrl must use https',
  })

export const deployRequestSchema = z.object({
  instanceUrl: httpsUrlSchema,
  username: z.string().min(1),
  password: z.string().min(1),
  scopeId: z.string().min(1),
  scopeName: z.string().min(1),
  reinstall: z.boolean().optional().default(false),
  skipInstallFallback: z.boolean().optional().default(false),
  flowSpec: flowSpecSchema,
})

export const generateFlowSourceRequestSchema = z.object({
  flowSpec: flowSpecSchema,
})

export const deploySubflowRequestSchema = z.object({
  instanceUrl: httpsUrlSchema,
  username: z.string().min(1),
  password: z.string().min(1),
  scopeId: z.string().min(1),
  scopeName: z.string().min(1),
  reinstall: z.boolean().optional().default(false),
  skipInstallFallback: z.boolean().optional().default(false),
  subflowSpec: subflowSpecSchema,
})

export const generateSubflowSourceRequestSchema = z.object({
  subflowSpec: subflowSpecSchema,
})

export const verifyFlowRequestSchema = z.object({
  instanceUrl: httpsUrlSchema,
  username: z.string().min(1),
  password: z.string().min(1),
  flowName: z.string().min(1),
})

export const verifySubflowRequestSchema = z.object({
  instanceUrl: httpsUrlSchema,
  username: z.string().min(1),
  password: z.string().min(1),
  flowName: z.string().min(1),
})

export const availableAppsRequestSchema = z.object({
  instanceUrl: httpsUrlSchema,
  username: z.string().min(1),
  password: z.string().min(1),
  limit: z.number().int().min(1).max(100).optional().default(25),
})

export const availableFlowsRequestSchema = z.object({
  instanceUrl: httpsUrlSchema,
  username: z.string().min(1),
  password: z.string().min(1),
  flowName: z.string().min(1).optional(),
  scopeId: z.string().min(1).optional(),
  limit: z.number().int().min(1).max(100).optional().default(25),
})

export type DeployRequest = z.infer<typeof deployRequestSchema>
export type GenerateFlowSourceRequest = z.infer<typeof generateFlowSourceRequestSchema>
export type DeploySubflowRequest = z.infer<typeof deploySubflowRequestSchema>
export type GenerateSubflowSourceRequest = z.infer<typeof generateSubflowSourceRequestSchema>
export type VerifyFlowRequest = z.infer<typeof verifyFlowRequestSchema>
export type VerifySubflowRequest = z.infer<typeof verifySubflowRequestSchema>
export type AvailableAppsRequest = z.infer<typeof availableAppsRequestSchema>
export type AvailableFlowsRequest = z.infer<typeof availableFlowsRequestSchema>
