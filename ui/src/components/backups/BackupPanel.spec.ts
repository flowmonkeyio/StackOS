import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { configureBackups, createBackup, loadBackupStatus, type BackupStatus } from '@/lib/backups'
import BackupPanel from './BackupPanel.vue'

vi.mock('@/lib/backups', () => ({
  configureBackups: vi.fn(),
  createBackup: vi.fn(),
  loadBackupStatus: vi.fn(),
}))
function fixture(patch: Partial<BackupStatus> = {}): BackupStatus {
  return {
    configured: false,
    ready: false,
    enabled: false,
    interval_hours: 24,
    keep_last: 30,
    destination: '/private/backups',
    targets: [],
    project_ids: [],
    recipient_fingerprint: null,
    last_attempt: null,
    last_success: null,
    last_error: null,
    ...patch,
  }
}
beforeEach(() => {
  vi.mocked(loadBackupStatus).mockReset().mockResolvedValue(fixture())
  vi.mocked(configureBackups).mockReset().mockResolvedValue(fixture())
  vi.mocked(createBackup).mockReset().mockResolvedValue()
})

describe('local backup console', () => {
  it('rejects a private PEM before any console operation receives it', async () => {
    const wrapper = mount(BackupPanel)
    await flushPromises()
    await wrapper
      .get('textarea')
      .setValue('-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('[role="alert"]').text()).toContain('Use only an X25519 PUBLIC KEY PEM')
    expect(configureBackups).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('starts with opt-in off and automatic project Finance coverage', async () => {
    const wrapper = mount(BackupPanel)
    expect(wrapper.find('.ui-skeleton').exists()).toBe(true)
    await flushPromises()
    expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false)
    expect(wrapper.text()).toContain(
      'Project Finance records, invoices, receipts and reports are included automatically.',
    )
    expect(wrapper.text()).toContain('No managed project files are present yet.')
    expect(wrapper.text()).toContain('None recorded')
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    expect(wrapper.get('textarea').attributes('placeholder')).toBe('-----BEGIN PUBLIC KEY-----')
    expect(configureBackups).not.toHaveBeenCalled()
    expect(
      wrapper
        .findAll('button')
        .find((button) => button.text() === 'Back up now')
        ?.attributes('disabled'),
    ).toBeDefined()
    wrapper.unmount()
  })
  it('shows separately covered projects without enrolling their files as external folders', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(
      fixture({ configured: true, ready: true, project_ids: [1, 2] }),
    )
    const wrapper = mount(BackupPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('Covered project IDs: 1, 2.')
    expect(wrapper.text()).toContain('No additional external folders are enrolled.')
    expect(configureBackups).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('does not claim project files are absent when coverage inspection failed', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(
      fixture({
        project_ids: null,
        last_error: {
          code: 'source_unavailable',
          message: 'A coordination lock is missing.',
          next_action: 'Repair project storage.',
        },
      }),
    )
    const wrapper = mount(BackupPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('Project coverage could not be verified.')
    expect(wrapper.text()).not.toContain('No managed project files are present yet.')
    wrapper.unmount()
  })
  it('requires explicit save for enable/disable and preserves external coverage', async () => {
    const value = fixture({
      configured: true,
      ready: true,
      targets: [
        {
          id: 'finance',
          label: 'Business bills',
          path: '/private/finance',
          lock_file: '/private/finance/.writer.lock',
        },
      ],
    })
    vi.mocked(loadBackupStatus).mockResolvedValue(value)
    vi.mocked(configureBackups).mockResolvedValue({ ...value, enabled: true })
    const wrapper = mount(BackupPanel)
    await flushPromises()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    expect(configureBackups).not.toHaveBeenCalled()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(configureBackups).toHaveBeenCalledWith({
      enabled: true,
    })
    expect(wrapper.text()).toContain('Business bills')
    expect(wrapper.text()).toContain('Backup settings saved.')
    await wrapper.get('input[type="checkbox"]').setValue(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(configureBackups).toHaveBeenLastCalledWith({
      enabled: false,
    })
    wrapper.unmount()
  })
  it('sends only the disable when unchanged coverage has become unsafe', async () => {
    const value = fixture({
      configured: true,
      enabled: true,
      ready: false,
      interval_hours: 12,
      keep_last: 7,
      last_error: {
        code: 'unsafe_path',
        message: 'Backup paths must be absolute and cannot be symlinks.',
        next_action: 'Check backup configuration.',
      },
    })
    vi.mocked(loadBackupStatus).mockResolvedValue(value)
    vi.mocked(configureBackups).mockResolvedValue({ ...value, enabled: false })
    const wrapper = mount(BackupPanel)
    await flushPromises()
    await wrapper.get('input[type="checkbox"]').setValue(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(configureBackups).toHaveBeenCalledTimes(1)
    expect(configureBackups).toHaveBeenCalledWith({ enabled: false })
    expect(wrapper.text()).toContain('Automatic backups are off')
    expect(wrapper.text()).toContain('Backup settings saved.')
    wrapper.unmount()
  })
  it('preserves simultaneous schedule and public key changes when disabling', async () => {
    const value = fixture({ configured: true, enabled: true, ready: true })
    const publicKey = '-----BEGIN PUBLIC KEY-----\nc3ludGhldGlj\n-----END PUBLIC KEY-----'
    vi.mocked(loadBackupStatus).mockResolvedValue(value)
    const wrapper = mount(BackupPanel)
    await flushPromises()
    await wrapper.get('input[type="checkbox"]').setValue(false)
    const numbers = wrapper.findAll('input[type="number"]')
    await numbers[0].setValue('12')
    await numbers[1].setValue('7')
    await wrapper.get('textarea').setValue(publicKey)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(configureBackups).toHaveBeenCalledTimes(1)
    expect(configureBackups).toHaveBeenCalledWith({
      enabled: false,
      interval_hours: 12,
      keep_last: 7,
      recipient_public_key: publicKey,
    })
    wrapper.unmount()
  })
  it('validates limits and displays repair while retaining previous successful backup', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(
      fixture({
        configured: true,
        ready: true,
        last_success: { created_at: '2026-10-13T10:00:00Z', path: '/private/backups/good.sobak' },
        last_error: {
          code: 'busy',
          message: 'Finance writer is busy.',
          next_action: 'Try after it finishes.',
        },
      }),
    )
    const wrapper = mount(BackupPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('Finance writer is busy. Try after it finishes.')
    expect(wrapper.text()).toContain('good.sobak')
    await wrapper.get('input[type="number"]').setValue('0')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('[role="alert"]').text()).toContain('1–168 hours')
    expect(configureBackups).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('shows status fetch failure and offers a keyboard reachable refresh', async () => {
    vi.mocked(loadBackupStatus).mockRejectedValueOnce(new Error('Local console unavailable'))
    const wrapper = mount(BackupPanel)
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Local console unavailable')
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Automatic backups are off')
    wrapper.unmount()
  })
})
