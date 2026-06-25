/**
 * Typed, feature-detected bridge to the Electron desktop shell.
 *
 * The shell injects `window.stackosDesktop` (see desktop/src/preload.js). It is
 * present ONLY inside the packaged app — in a plain browser the global is
 * undefined, so every accessor here guards with `isDesktopShell()` and returns
 * `null` rather than throwing. UI surfaces should hide shell-only affordances
 * (restart service, install/repair, run doctor, updates) when not in the shell.
 */

export interface DesktopHealth {
  db_status?: string
  scheduler_running?: boolean
  version?: string
  daemon_uptime_s?: number
  [key: string]: unknown
}

export interface DesktopStatus {
  health?: DesktopHealth | null
  command?: { mode?: string; [key: string]: unknown } | null
  payload?: { name?: string; version?: string; buildId?: string; builtAt?: string } | null
  notifications?: { status?: string; [key: string]: unknown } | null
  [key: string]: unknown
}

export interface DesktopCommandResult {
  ok: boolean
  code?: number
  message?: string
  detail?: unknown
  [key: string]: unknown
}

export interface DesktopUpdateState {
  enabled?: boolean
  status: string
  reason?: string | null
  progress?: { percent?: number; [key: string]: unknown } | null
  updateInfo?: unknown
  lastError?: string | null
  updateUrl?: string | null
  [key: string]: unknown
}

export interface DesktopUpdateResult extends DesktopCommandResult {
  state?: DesktopUpdateState
  updateInfo?: unknown
  reason?: string
}

interface DesktopBridge {
  status(): Promise<DesktopStatus>
  installOrRepair(): Promise<DesktopCommandResult>
  restartService(): Promise<DesktopCommandResult>
  runDoctor(): Promise<DesktopCommandResult>
  checkForUpdates(): Promise<DesktopUpdateResult>
  downloadUpdate(): Promise<DesktopUpdateResult>
  installUpdate(): Promise<DesktopUpdateResult>
  updateState(): Promise<DesktopUpdateState>
}

function bridge(): DesktopBridge | null {
  if (typeof window === 'undefined') return null
  const candidate = (window as unknown as { stackosDesktop?: Partial<DesktopBridge> }).stackosDesktop
  if (!candidate || typeof candidate.status !== 'function') return null
  return candidate as DesktopBridge
}

/** True when running inside the packaged Electron shell (not a browser). */
export function isDesktopShell(): boolean {
  return bridge() !== null
}

async function safe<T>(fn: (b: DesktopBridge) => Promise<T>): Promise<T | null> {
  const b = bridge()
  if (!b) return null
  try {
    return await fn(b)
  } catch {
    return null
  }
}

export const desktop = {
  isShell: isDesktopShell,
  status: () => safe((b) => b.status()),
  installOrRepair: () => safe((b) => b.installOrRepair()),
  restartService: () => safe((b) => b.restartService()),
  runDoctor: () => safe((b) => b.runDoctor()),
  checkForUpdates: () => safe((b) => b.checkForUpdates()),
  downloadUpdate: () => safe((b) => b.downloadUpdate()),
  installUpdate: () => safe((b) => b.installUpdate()),
  updateState: () => safe((b) => b.updateState()),
}
