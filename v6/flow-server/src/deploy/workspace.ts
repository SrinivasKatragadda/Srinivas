import { mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { toSafeNowId } from '../generator/ids.js'
import { fluentDir, nowConfigPath, projectRoot } from './paths.js'

/**
 * One SDK workspace per ServiceNow application.
 *
 * The SDK records every installed record in src/fluent/generated/keys.ts and, on the next install, DELETES any
 * record that is no longer in the source. Writing a single generated file per deploy therefore removed the
 * previously deployed flow/subflow of the same app. Instead every flow and subflow keeps its own file inside the
 * workspace of its application, so each install contains everything built for that app.
 *
 * The active workspace lives in src/fluent; other apps' workspaces are parked in .workspaces/<scope sys_id>/.
 */

export type ScopeConfig = {
  scope: string
  name: string
  scopeId: string
}

const parkedRoot = join(projectRoot, '.workspaces')

let busy = false

export function isDeployInProgress(): boolean {
  return busy
}

/** One deployment at a time: flows and subflows share the SDK project directory. */
export function acquireDeployLock(): void {
  if (busy) {
    const conflict = new Error('Another deployment is already running')
    conflict.name = 'DeployConflictError'
    throw conflict
  }
  busy = true
}

export function releaseDeployLock(): void {
  busy = false
}

async function exists(path: string): Promise<boolean> {
  try {
    await stat(path)
    return true
  } catch {
    return false
  }
}

async function currentScopeId(): Promise<string | null> {
  try {
    const config = JSON.parse(await readFile(nowConfigPath, 'utf8')) as { scopeId?: string }
    return config.scopeId ?? null
  } catch {
    return null
  }
}

/** Make src/fluent the workspace of `scope` (parking the previous app's workspace) and write now.config.json. */
export async function activateWorkspace(scope: ScopeConfig): Promise<void> {
  const active = await currentScopeId()
  if (active && active !== scope.scopeId && (await exists(fluentDir))) {
    const parked = join(parkedRoot, active)
    await mkdir(parkedRoot, { recursive: true })
    await rm(parked, { recursive: true, force: true })
    await rename(fluentDir, parked)
  }
  if (active !== scope.scopeId || !(await exists(fluentDir))) {
    const parked = join(parkedRoot, scope.scopeId)
    if (await exists(parked)) {
      await rename(parked, fluentDir)
    }
  }
  await mkdir(fluentDir, { recursive: true })

  const nowConfig = {
    scope: scope.scope,
    name: scope.name,
    scopeId: scope.scopeId,
    fluentDir: 'src/fluent',
    clientDir: '',
    metadataDir: 'metadata',
    appOutputDir: 'dist/app',
    packOutputDir: 'target',
    tsconfigPath: 'tsconfig.json',
  }
  await writeFile(nowConfigPath, `${JSON.stringify(nowConfig, null, 2)}\n`, 'utf8')
}

export type WrittenItem = {
  path: string
  /** Put the file back as it was before this deploy (used when the build or install fails). */
  revert: () => Promise<void>
}

/** Write (or replace) the source file of one flow/subflow in the active workspace. */
export async function writeItemSource(kind: 'flow' | 'subflow', id: string, source: string): Promise<WrittenItem> {
  const path = join(fluentDir, `${kind}_${toSafeNowId(id)}.now.ts`)
  const previous = (await exists(path)) ? await readFile(path, 'utf8') : null
  await writeFile(path, source, 'utf8')
  return {
    path,
    revert: async () => {
      if (previous === null) {
        await rm(path, { force: true })
      } else {
        await writeFile(path, previous, 'utf8')
      }
    },
  }
}
