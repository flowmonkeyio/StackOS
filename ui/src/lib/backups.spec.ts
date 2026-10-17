import { beforeEach, describe, expect, it, vi } from 'vitest'
import { callOperation } from '@/lib/operations'
import { configureBackups, createBackup, loadBackupStatus } from './backups'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
beforeEach(() => vi.mocked(callOperation).mockReset())

describe('local backup operations client', () => {
  it('loads safe status without project or workflow authority', async () => {
    vi.mocked(callOperation).mockResolvedValue({ enabled: false })
    expect(await loadBackupStatus()).toEqual({ enabled: false })
    expect(callOperation).toHaveBeenCalledWith('backup.status', {})
  })
  it('patches only explicit fields and unwraps safe configuration', async () => {
    vi.mocked(callOperation).mockResolvedValue({ data: { enabled: true } })
    expect(await configureBackups({ enabled: true })).toEqual({ enabled: true })
    expect(callOperation).toHaveBeenCalledWith('backup.configure', { enabled: true })
  })
  it('requests a backup at the configured private destination', async () => {
    await createBackup()
    expect(callOperation).toHaveBeenCalledWith('backup.create', {})
  })
})
