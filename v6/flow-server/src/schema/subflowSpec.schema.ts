import { z } from 'zod'
import { actionSchema, dataValueSchema } from './flowSpec.schema.js'

export const subflowIoSchema = z.object({
  type: z.enum(['string', 'integer', 'boolean']),
  label: z.string().min(1),
  mandatory: z.boolean().optional().default(false),
})

export const subflowSpecSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  description: z.string().optional().default(''),
  runAs: z.enum(['system', 'user']).default('system'),
  inputs: z.record(z.string(), subflowIoSchema).optional().default({}),
  outputs: z.record(z.string(), subflowIoSchema).optional().default({}),
  outputValues: z.record(dataValueSchema).optional().default({}),
  actions: z.array(actionSchema).min(1),
})

export type SubflowIoSpec = z.infer<typeof subflowIoSchema>
export type SubflowSpec = z.infer<typeof subflowSpecSchema>
