// Tear the daemon down + clean tmp state.

import { rmSync } from 'node:fs'

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

export default async function globalTeardown(): Promise<void> {
  const rec = globalThis.__STACKOS_DAEMON__
  if (!rec) return
  if (rec.pid > 0) {
    // Detached child → kill the whole process group so uv + python + uvicorn
    // all exit (otherwise port 5181 stays held).
    for (const sig of ['SIGTERM', 'SIGKILL'] as const) {
      try {
        process.kill(-rec.pid, sig)
      } catch {
        /* gone */
      }
    }
  }
  for (const dir of [rec.stateDir, rec.dataDir]) {
    try {
      rmSync(dir, { recursive: true, force: true })
    } catch {
      /* best-effort */
    }
  }
}
