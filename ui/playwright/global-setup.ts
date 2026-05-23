// Spawn the StackOS daemon on a separate port so it doesn't
// collide with a developer's `make serve`. Wait until /health responds
// 200 before letting any tests run. Persist the spawned PID + state-dir
// path on `globalThis` so the teardown can stop the daemon and exfiltrate
// the auth token for tests that need direct API access.

import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const REPO_ROOT = join(__dirname, '..', '..')

const PORT = Number(process.env.STACKOS_E2E_PORT ?? 5181)
const SERVE_ARGS = ['-m', 'stackos', 'serve', '--host', '127.0.0.1', '--port', String(PORT)]

interface DaemonRecord {
  pid: number
  stateDir: string
  dataDir: string
  token: string
}

declare global {
  // eslint-disable-next-line no-var
  var __STACKOS_DAEMON__: DaemonRecord | undefined
}

async function probeHealth(url: string, timeoutMs = 30_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url)
      if (res.ok) return true
    } catch {
      /* still booting */
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  return false
}

async function readToken(stateDir: string, timeoutMs = 30_000): Promise<string | null> {
  const tokenPath = join(stateDir, 'auth.token')
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (existsSync(tokenPath)) {
      try {
        const t = readFileSync(tokenPath, 'utf-8').trim()
        if (t.length > 0) return t
      } catch {
        /* may still be writing */
      }
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  return null
}

function daemonCommand(): { command: string; args: string[] } {
  if (process.env.STACKOS_E2E_PYTHON) {
    return { command: process.env.STACKOS_E2E_PYTHON, args: SERVE_ARGS }
  }

  const venvPython = join(REPO_ROOT, '.venv', 'bin', 'python')
  if (existsSync(venvPython)) {
    return { command: venvPython, args: SERVE_ARGS }
  }

  return { command: 'uv', args: ['run', 'python', ...SERVE_ARGS] }
}

export default async function globalSetup(): Promise<void> {
  const stateDir = mkdtempSync(join(tmpdir(), 'stackos-e2e-state-'))
  const dataDir = mkdtempSync(join(tmpdir(), 'stackos-e2e-data-'))

  const env = {
    ...process.env,
    STACKOS_STATE_DIR: stateDir,
    STACKOS_DATA_DIR: dataDir,
    STACKOS_PORT: String(PORT),
    PYTHONUNBUFFERED: '1',
  }

  const daemon = daemonCommand()
  const child = spawn(daemon.command, daemon.args, {
    cwd: REPO_ROOT,
    env,
    stdio: 'pipe',
    // Run in its own process group so the teardown can SIGKILL the
    // whole tree (uv -> python -> uvicorn worker) at once.
    detached: true,
  })
  // Surface daemon stderr/stdout to the test runner's output for triage.
  child.stdout?.on('data', (d) => process.stdout.write(`[daemon] ${d}`))
  child.stderr?.on('data', (d) => process.stderr.write(`[daemon] ${d}`))

  const ok = await probeHealth(`http://127.0.0.1:${PORT}/api/v1/health`, 60_000)
  if (!ok) {
    if (child.pid !== undefined) {
      try {
        process.kill(-child.pid, 'SIGTERM')
      } catch {
        /* fall through */
      }
    }
    throw new Error(`daemon never became healthy on :${PORT}`)
  }

  const token = await readToken(stateDir, 5_000)
  if (!token) {
    child.kill('SIGTERM')
    throw new Error(`token file never appeared at ${stateDir}/auth.token`)
  }

  globalThis.__STACKOS_DAEMON__ = {
    pid: child.pid ?? -1,
    stateDir,
    dataDir,
    token,
  }
  process.env.STACKOS_E2E_TOKEN = token
  process.env.STACKOS_E2E_BASE_URL = `http://127.0.0.1:${PORT}`
}
