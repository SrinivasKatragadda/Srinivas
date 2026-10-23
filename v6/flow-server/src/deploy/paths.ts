import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const sourceDir = dirname(fileURLToPath(import.meta.url))
export const projectRoot = join(sourceDir, '..', '..')
export const fluentDir = join(projectRoot, 'src', 'fluent')
export const generatedFlowPath = join(fluentDir, 'generated_flow.now.ts')
export const generatedSubflowPath = join(fluentDir, 'generated_subflow.now.ts')
export const nowConfigPath = join(projectRoot, 'now.config.json')
