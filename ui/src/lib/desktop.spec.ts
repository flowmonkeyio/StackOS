import { afterEach, describe, expect, it, vi } from 'vitest'
import { desktop, type DesktopBackupAction, type DesktopBackupResult } from './desktop'

const actions: DesktopBackupAction[] = [
  'setupBackups',
  'openBackupFolder',
  'exportLatestBackup',
  'exportRecoveryKey',
  'restoreBackup',
]
afterEach(() => vi.unstubAllGlobals())

describe('native backup bridge', () => {
  it.each(actions)('guards %s in a browser and an older desktop shell', async (action) => {
    vi.stubGlobal('stackosDesktop', undefined)
    expect(desktop.isShell()).toBe(false)
    expect(desktop.hasBackupAction(action)).toBe(false)
    expect(await desktop[action]()).toBeNull()
    vi.stubGlobal('stackosDesktop', { status: vi.fn() })
    expect(desktop.isShell()).toBe(true)
    expect(desktop.hasBackupAction(action)).toBe(false)
    expect(await desktop[action]()).toBeNull()
  })

  it.each(actions)('passes no arguments through %s and returns its safe result', async (action) => {
    const result: DesktopBackupResult = {
      ok: true,
      status: 'completed',
      message: 'Saved.',
      outputPath: '/selected/output',
    }
    const method = vi.fn().mockResolvedValue(result)
    vi.stubGlobal('stackosDesktop', { status: vi.fn(), [action]: method })
    expect(desktop.hasBackupAction(action)).toBe(true)
    expect(await desktop[action]()).toEqual(result)
    expect(method).toHaveBeenCalledTimes(1)
    expect(method).toHaveBeenCalledWith()
  })

  it.each(['completed', 'cancelled', 'partial', 'failed'] as const)(
    'preserves the %s result contract',
    async (status) => {
      const result: DesktopBackupResult = {
        ok: status === 'completed',
        status,
        message: 'Safe result.',
        code: 'result_code',
      }
      vi.stubGlobal('stackosDesktop', {
        status: vi.fn(),
        setupBackups: vi.fn().mockResolvedValue(result),
      })
      expect(await desktop.setupBackups()).toEqual(result)
    },
  )

  it('contains bridge rejection details', async () => {
    vi.stubGlobal('stackosDesktop', {
      status: vi.fn(),
      exportRecoveryKey: vi.fn().mockRejectedValue(new Error('Native diagnostic.')),
    })
    expect(await desktop.exportRecoveryKey()).toBeNull()
  })
})
