import { z } from 'zod'

export const primitiveValueSchema = z.union([z.string(), z.number(), z.boolean(), z.null()])

export const templateValueSchema = z.object({
  template: z.string().min(1),
})

export const dataValueSchema: z.ZodType<FlowInputValue> = z.lazy(() =>
  z.union([
    primitiveValueSchema,
    templateValueSchema,
    z.array(dataValueSchema),
    z.record(dataValueSchema),
  ])
)

export type FlowInputValue =
  | z.infer<typeof primitiveValueSchema>
  | { template: string }
  | FlowInputValue[]
  | { [key: string]: FlowInputValue }

export const triggerSchema = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('record.created'),
    table: z.string().min(1),
    condition: z.string().optional().default(''),
  }),
  z.object({
    type: z.literal('record.updated'),
    table: z.string().min(1),
    condition: z.string().optional().default(''),
  }),
  z.object({
    type: z.literal('record.created_or_updated'),
    table: z.string().min(1),
    condition: z.string().optional().default(''),
  }),
])

export const actionSchema = z.union([
  z.discriminatedUnion('type', [
    z.object({
      id: z.string().min(1),
      type: z.literal('core.log'),
      inputs: z.object({
        log_level: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
        log_message: dataValueSchema,
      }),
    }),
    z.object({
      id: z.string().min(1),
      type: z.literal('core.sendEmail'),
      inputs: z.object({
        table_name: dataValueSchema,
        record: dataValueSchema,
        watermark_email: dataValueSchema.optional(),
        ah_to: dataValueSchema,
        ah_cc: dataValueSchema.optional(),
        ah_bcc: dataValueSchema.optional(),
        ah_subject: dataValueSchema,
        ah_body: dataValueSchema,
      }),
    }),
    z.object({
      id: z.string().min(1),
      type: z.literal('core.updateRecord'),
      inputs: z.object({
        table: dataValueSchema,
        record: dataValueSchema,
        fields: z.record(dataValueSchema),
      }),
    }),
    z.object({
      id: z.string().min(1),
      type: z.literal('core.lookUpRecord'),
      inputs: z.object({
        table: dataValueSchema,
        conditions: dataValueSchema.optional(),
        encoded_query: dataValueSchema.optional(),
        sort_type: dataValueSchema.optional(),
        if_multiple_records_are_found_action: dataValueSchema.optional(),
      }),
    }),
  ]),
  z.object({
    id: z.string().min(1),
    type: z.string().min(1),
    inputs: z.record(z.string(), dataValueSchema).default({}),
  })
])

export const flowSpecSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  description: z.string().optional().default(''),
  runAs: z.enum(['system', 'user']).default('system'),
  trigger: triggerSchema,
  actions: z.array(actionSchema).min(1),
})

export type TriggerSpec = z.infer<typeof triggerSchema>
export type ActionSpec = z.infer<typeof actionSchema>
export type FlowSpec = z.infer<typeof flowSpecSchema>
