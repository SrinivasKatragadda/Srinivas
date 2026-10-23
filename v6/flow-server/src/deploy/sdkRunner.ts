import { spawn } from 'node:child_process'
import { redactSecret } from '../generator/sanitize.js'
import { projectRoot } from './paths.js'

export type SdkCredentials = {
  instanceUrl: string
  username: string
  password: string
}

export type SdkRunResult = {
  command: string
  exitCode: number
  stdout: string
  stderr: string
}

const MAX_LOG_LENGTH = 30_000

export async function runSdkCommand(args: string[], credentials: SdkCredentials): Promise<SdkRunResult> {
  const spawnCommand = getSpawnCommand(args)
  const command = `npx${process.platform === 'win32' ? '.cmd' : ''} ${args.join(' ')}`

  return new Promise((resolve, reject) => {
    const child = spawn(spawnCommand.executable, spawnCommand.args, {
      cwd: projectRoot,
      shell: false,
      env: {
        ...process.env,
        SN_SDK_INSTANCE_URL: credentials.instanceUrl,
        SN_SDK_USER: credentials.username,
        SN_SDK_USER_PWD: credentials.password,
        SN_SDK_NODE_ENV: 'SN_SDK_CI_INSTALL',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })

    let stdout = ''
    let stderr = ''

    child.stdout.on('data', (chunk: Buffer) => {
      stdout = appendBounded(stdout, redactSecret(chunk.toString('utf8'), credentials.password))
    })

    child.stderr.on('data', (chunk: Buffer) => {
      stderr = appendBounded(stderr, redactSecret(chunk.toString('utf8'), credentials.password))
    })

    child.on('error', (error) => {
      reject(error)
    })

    child.on('close', (exitCode) => {
      resolve({
        command,
        exitCode: exitCode ?? 1,
        stdout,
        stderr,
      })
    })
  })
}

function getSpawnCommand(args: string[]): { executable: string; args: string[] } {
  if (process.platform !== 'win32') {
    return {
      executable: 'npx',
      args,
    }
  }

  return {
    executable: process.env.ComSpec ?? 'C:\\Windows\\System32\\cmd.exe',
    args: ['/d', '/s', '/c', 'npx.cmd', ...args],
  }
}

function appendBounded(existing: string, next: string): string {
  const combined = existing + next
  if (combined.length <= MAX_LOG_LENGTH) {
    return combined
  }

  return combined.slice(combined.length - MAX_LOG_LENGTH)
}
