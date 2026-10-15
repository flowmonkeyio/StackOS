import { callOperation } from '@/lib/operations'

export interface BackupTarget {
  id: string
  label: string
  path: string
  lock_file: string | null
}

export interface BackupOutcome {
  status?: string
  created_at?: string
  started_at?: string
  path?: string
  sha256?: string
  bytes?: number
}

export interface BackupStatus {
  configured: boolean
  ready: boolean
  enabled: boolean
  interval_hours: number
  keep_last: number
  destination: string
  targets: BackupTarget[]
  recipient_fingerprint: string | null
  last_attempt: BackupOutcome | null
  last_success: BackupOutcome | null
  last_error: { code: string; message: string; next_action: string } | null
}

export interface BackupPatch {
  enabled?: boolean
  interval_hours?: number
  keep_last?: number
  recipient_public_key?: string
}

export function loadBackupStatus(): Promise<BackupStatus> {
  return callOperation<BackupStatus>('backup.status', {})
}

export async function configureBackups(patch: BackupPatch): Promise<BackupStatus> {
  const value = await callOperation<{ data: BackupStatus }>('backup.configure', { ...patch })
  return value.data
}

export async function createBackup(): Promise<void> {
  await callOperation('backup.create', {})
}
