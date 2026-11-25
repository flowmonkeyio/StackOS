import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { configureBackups, createBackup, loadBackupStatus, type BackupStatus } from '@/lib/backups'
import type { DesktopBackupResult } from '@/lib/desktop'
import BackupPanel from './BackupPanel.vue'

vi.mock('@/lib/backups', () => ({
  configureBackups: vi.fn(),
  createBackup: vi.fn(),
  loadBackupStatus: vi.fn(),
}))

const native = {
  status: vi.fn(),
  setupBackups: vi.fn<() => Promise<DesktopBackupResult>>(),
  openBackupFolder: vi.fn<() => Promise<DesktopBackupResult>>(),
  exportLatestBackup: vi.fn<() => Promise<DesktopBackupResult>>(),
  exportRecoveryKey: vi.fn<() => Promise<DesktopBackupResult>>(),
  restoreBackup: vi.fn<() => Promise<DesktopBackupResult>>(),
}

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

function configured(patch: Partial<BackupStatus> = {}): BackupStatus {
  return fixture({
    configured: true,
    ready: true,
    enabled: true,
    interval_hours: 6,
    keep_last: 11,
    destination: '/custom/backups',
    targets: [{ id: 'books', label: 'Books', path: '/custom/books', lock_file: null }],
    ...patch,
  })
}

function button(wrapper: VueWrapper, label: string) {
  const result = wrapper
    .findAll('button')
    .find((item) => item.text() === label || item.attributes('aria-label') === label)
  if (!result) throw new Error('Missing button: ' + label)
  return result
}

function mountPanel(options: { attachTo?: Element | string } = {}) {
  return mount(BackupPanel, { ...options, global: { stubs: { teleport: true } } })
}
async function chooseManage(wrapper: VueWrapper, label: string) {
  await button(wrapper, 'Manage').trigger('click')
  await button(wrapper, label).trigger('click')
  await flushPromises()
}
async function closePanel(wrapper: VueWrapper) {
  await button(wrapper, 'Close panel').trigger('click')
  await flushPromises()
}
function numberValues(wrapper: VueWrapper) {
  return wrapper
    .findAll('input[type="number"]')
    .map((input) => (input.element as HTMLInputElement).value)
}

function loseFocusToBody() {
  // jsdom does not drop focus when a focused button becomes disabled.
  const temporary = document.createElement('button')
  document.body.append(temporary)
  temporary.focus()
  temporary.remove()
}

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.mocked(loadBackupStatus).mockReset().mockResolvedValue(fixture())
  vi.mocked(configureBackups)
    .mockReset()
    .mockImplementation(async (patch) => ({ ...configured(), ...patch }))
  vi.mocked(createBackup).mockReset().mockResolvedValue()
  for (const action of Object.values(native)) {
    action
      .mockReset()
      .mockResolvedValue({ ok: true, status: 'completed', message: 'Action complete.' })
  }
  vi.stubGlobal('stackosDesktop', native)
})
afterEach(() => vi.unstubAllGlobals())

describe('local backup console', () => {
  it('opening an existing custom policy only reads status', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    expect(loadBackupStatus).toHaveBeenCalledTimes(1)
    expect(configureBackups).not.toHaveBeenCalled()
    expect(createBackup).not.toHaveBeenCalled()
    for (const action of Object.values(native)) expect(action).not.toHaveBeenCalled()
    expect(numberValues(wrapper)).toEqual(['6', '11'])
    expect(wrapper.text()).toContain('/custom/backups')
    expect(wrapper.text()).not.toContain('Enable backups')
    await closePanel(wrapper)
    await chooseManage(wrapper, 'Included data and history')
    expect(wrapper.text()).toContain('Books')
    expect(configureBackups).not.toHaveBeenCalled()
  })

  it('unchanged settings do not write an existing policy', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    expect(button(wrapper, 'Save backup settings').attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(configureBackups).not.toHaveBeenCalled()
  })

  it('schedule-only edits do not resubmit enabled or other policy fields', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured({ interval_hours: 24 }))
    const wrapper = mountPanel()
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    await wrapper.get('input[type="number"]').setValue('6')
    await wrapper.get('form').trigger('submit')
    expect(configureBackups).toHaveBeenCalledWith({ interval_hours: 6 })
  })

  it('requires explicit save for enable/disable and preserves external coverage', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    await wrapper.get('input[type="checkbox"]').setValue(false)
    expect(configureBackups).not.toHaveBeenCalled()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(configureBackups).toHaveBeenCalledWith({ enabled: false })
    expect(wrapper.text()).toContain('Automatic backups paused')
    expect(wrapper.text()).toContain('Backup settings saved.')
    expect(numberValues(wrapper)).toEqual(['6', '11'])
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(configureBackups).toHaveBeenLastCalledWith({ enabled: true })
  })

  it('sends only the disable when unchanged coverage has become unsafe', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(
      configured({
        ready: false,
        last_error: {
          code: 'unsafe_path',
          message: 'A backup path is unsafe.',
          next_action: 'Check backup configuration.',
        },
      }),
    )
    const wrapper = mountPanel()
    await flushPromises()
    expect(button(wrapper, 'Back up now').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('A backup path is unsafe.')
    await chooseManage(wrapper, 'Backup settings')
    await wrapper.get('input[type="checkbox"]').setValue(false)
    await wrapper.get('form').trigger('submit')
    expect(configureBackups).toHaveBeenCalledWith({ enabled: false })
  })

  it('saves simultaneous explicit changes and validates limits', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    const numbers = wrapper.findAll('input[type="number"]')
    await numbers[0].setValue('0')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('[role="alert"]').text()).toContain('1–168 hours')
    expect(configureBackups).not.toHaveBeenCalled()
    await numbers[0].setValue('12')
    await numbers[1].setValue('7')
    await wrapper.get('input[type="checkbox"]').setValue(false)
    await wrapper.get('form').trigger('submit')
    expect(configureBackups).toHaveBeenCalledWith({
      enabled: false,
      interval_hours: 12,
      keep_last: 7,
    })
  })

  it('retains entered settings when saving fails', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    vi.mocked(configureBackups).mockRejectedValue(new Error('Settings could not be saved.'))
    const wrapper = mountPanel()
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    await wrapper.get('input[type="number"]').setValue('12')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Settings could not be saved.')
    expect(numberValues(wrapper)).toEqual(['12', '11'])
    expect(button(wrapper, 'Save backup settings').attributes('disabled')).toBeUndefined()
  })

  it('waits for status, then offers native setup and restore without key fields or commands', async () => {
    const wrapper = mountPanel()
    expect(wrapper.find('.ui-skeleton').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Enable backups')
    await flushPromises()
    expect(button(wrapper, 'Enable backups').attributes('disabled')).toBeUndefined()
    expect(button(wrapper, 'Restore backup').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.find('input').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('stackos backups')
    expect(wrapper.text()).not.toContain('PUBLIC KEY')
    expect(configureBackups).not.toHaveBeenCalled()
    expect(native.setupBackups).not.toHaveBeenCalled()
  })

  it('withholds the Restore action until the first status read settles', async () => {
    let finish!: (value: BackupStatus) => void
    vi.mocked(loadBackupStatus).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const wrapper = mountPanel()
    expect(wrapper.findAll('button').some((control) => control.text() === 'Restore backup')).toBe(
      false,
    )
    expect(wrapper.find('.ui-skeleton').exists()).toBe(true)
    finish(configured())
    await flushPromises()
    expect(button(wrapper, 'Restore backup').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.ui-skeleton').exists()).toBe(false)
    expect(native.restoreBackup).not.toHaveBeenCalled()
  })

  it('preserves loaded content and button identity while refreshing', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValueOnce(configured())
    let finish!: (value: BackupStatus) => void
    vi.mocked(loadBackupStatus).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const wrapper = mountPanel()
    await flushPromises()
    const backupButton = button(wrapper, 'Back up now').element
    const restoreButton = button(wrapper, 'Restore backup').element
    await button(wrapper, 'Refresh backups').trigger('click')
    expect(wrapper.find('.ui-skeleton').exists()).toBe(false)
    expect(button(wrapper, 'Back up now').element).toBe(backupButton)
    expect(button(wrapper, 'Restore backup').element).toBe(restoreButton)
    expect(button(wrapper, 'Back up now').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Every 6 hours · Keep 11 backups')
    expect(wrapper.text()).toContain('/custom/backups')
    finish(configured())
    await flushPromises()
    expect(button(wrapper, 'Back up now').element).toBe(backupButton)
    expect(button(wrapper, 'Back up now').attributes('disabled')).toBeUndefined()
    expect(configureBackups).not.toHaveBeenCalled()
    expect(createBackup).not.toHaveBeenCalled()
  })

  it('runs native setup without renderer arguments or duplicate first backup', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValueOnce(fixture()).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Enable backups').trigger('click')
    await flushPromises()
    expect(native.setupBackups).toHaveBeenCalledTimes(1)
    expect(native.setupBackups).toHaveBeenCalledWith()
    expect(configureBackups).not.toHaveBeenCalled()
    expect(createBackup).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('Enable backups')
    expect(button(wrapper, 'Back up now').exists()).toBe(true)
    expect(wrapper.text()).toContain('Every 6 hours · Keep 11 backups')
  })

  it('keeps a cancelled first-time setup quiet without reloading or changing policy', async () => {
    native.setupBackups.mockResolvedValue({ ok: false, status: 'cancelled', message: 'Cancelled.' })
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Enable backups').trigger('click')
    await flushPromises()
    expect(loadBackupStatus).toHaveBeenCalledTimes(1)
    expect(configureBackups).not.toHaveBeenCalled()
    expect(createBackup).not.toHaveBeenCalled()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(button(wrapper, 'Enable backups').attributes('disabled')).toBeUndefined()
  })

  it('reloads partial setup and retries only ordinary backup creation', async () => {
    native.setupBackups.mockResolvedValue({
      ok: false,
      status: 'partial',
      message: 'Backups are set up, but the first backup failed.',
    })
    vi.mocked(loadBackupStatus).mockResolvedValueOnce(fixture()).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Enable backups').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="status"]').text()).toContain('first backup failed')
    expect(wrapper.text()).not.toContain('Enable backups')
    await button(wrapper, 'Back up now').trigger('click')
    await flushPromises()
    expect(createBackup).toHaveBeenCalledTimes(1)
    expect(createBackup).toHaveBeenCalledWith()
    expect(native.setupBackups).toHaveBeenCalledTimes(1)
    expect(configureBackups).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Encrypted backup created.')
  })

  it('reloads a failed setup so concurrent existing configuration stays authoritative', async () => {
    native.setupBackups.mockResolvedValue({
      ok: false,
      status: 'failed',
      message: 'Backups are already configured.',
      code: 'already_configured',
    })
    vi.mocked(loadBackupStatus).mockResolvedValueOnce(fixture()).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Enable backups').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('already configured')
    expect(wrapper.text()).not.toContain('Enable backups')
    expect(wrapper.text()).toContain('Every 6 hours · Keep 11 backups')
    expect(configureBackups).not.toHaveBeenCalled()
  })

  it('prevents another setup when status cannot be refreshed after partial success', async () => {
    native.setupBackups.mockResolvedValue({
      ok: false,
      status: 'partial',
      message: 'First backup failed.',
    })
    vi.mocked(loadBackupStatus)
      .mockResolvedValueOnce(fixture())
      .mockRejectedValue(new Error('Status unavailable.'))
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Enable backups').trigger('click')
    await flushPromises()
    expect(button(wrapper, 'Enable backups').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('Status unavailable.')
    expect(native.setupBackups).toHaveBeenCalledTimes(1)
  })

  it('disables concurrent work while native dialogs are open', async () => {
    let finish!: (value: DesktopBackupResult) => void
    native.setupBackups.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Enable backups').trigger('click')
    expect(wrapper.get('[role="status"]').text()).toContain('desktop window')
    for (const item of wrapper.findAll('button')) expect(item.attributes('disabled')).toBeDefined()
    await button(wrapper, 'Enable backups').trigger('click')
    expect(native.setupBackups).toHaveBeenCalledTimes(1)
    finish({ ok: false, status: 'cancelled', message: 'Cancelled.' })
    await flushPromises()
    expect(button(wrapper, 'Restore backup').attributes('disabled')).toBeUndefined()
  })

  it.each([
    ['Open backup folder', 'openBackupFolder'],
    ['Export backup', 'exportLatestBackup'],
    ['Export key', 'exportRecoveryKey'],
    ['Restore backup', 'restoreBackup'],
  ] as const)('invokes %s with no file, key or command arguments', async (label, action) => {
    vi.mocked(loadBackupStatus).mockResolvedValue(
      configured({
        last_success: { path: '/custom/backups/latest.sbak', created_at: '2026-10-13T10:00:00Z' },
      }),
    )
    native[action].mockResolvedValue({
      ok: true,
      status: 'completed',
      message: 'Saved.',
      outputPath: '/chosen/output',
    })
    const wrapper = mountPanel()
    await flushPromises()
    if (label === 'Open backup folder') await chooseManage(wrapper, label)
    else await button(wrapper, label).trigger('click')
    await flushPromises()
    expect(native[action]).toHaveBeenCalledTimes(1)
    expect(native[action]).toHaveBeenCalledWith()
    expect(wrapper.get('[role="status"]').text()).toContain('/chosen/output')
    expect(configureBackups).not.toHaveBeenCalled()
    expect(loadBackupStatus).toHaveBeenCalledTimes(1)
  })

  it('preserves unsaved settings and page state when a native recovery action is cancelled', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    native.restoreBackup.mockResolvedValue({
      ok: false,
      status: 'cancelled',
      message: 'Cancelled.',
    })
    const wrapper = mountPanel()
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    await wrapper.get('input[type="number"]').setValue('12')
    await closePanel(wrapper)
    await button(wrapper, 'Restore backup').trigger('click')
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    expect(numberValues(wrapper)).toEqual(['12', '11'])
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(loadBackupStatus).toHaveBeenCalledTimes(1)
    expect(configureBackups).not.toHaveBeenCalled()
  })

  it('does not offer export of an absent successful archive', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    expect(button(wrapper, 'Export backup').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('No successful backup yet')
  })

  it('displays native failure without exposing rejected bridge details', async () => {
    native.restoreBackup.mockRejectedValue(
      new Error('Private diagnostic must stay in native process.'),
    )
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Restore backup').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('The desktop action did not finish.')
    expect(wrapper.text()).not.toContain('Private diagnostic')
  })

  it('keeps safe backup and settings controls available in a browser', async () => {
    vi.stubGlobal('stackosDesktop', undefined)
    vi.mocked(loadBackupStatus).mockResolvedValue(configured({ enabled: false }))
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('Open the StackOS desktop app')
    expect(button(wrapper, 'Restore backup').attributes('disabled')).toBeDefined()
    expect(button(wrapper, 'Export key').attributes('disabled')).toBeDefined()
    expect(button(wrapper, 'Back up now').attributes('disabled')).toBeUndefined()
    await button(wrapper, 'Back up now').trigger('click')
    await flushPromises()
    expect(createBackup).toHaveBeenCalledTimes(1)
    expect(createBackup).toHaveBeenCalledWith()
    expect(native.setupBackups).not.toHaveBeenCalled()
  })

  it('explains updates for a desktop bridge missing the new methods', async () => {
    vi.stubGlobal('stackosDesktop', { status: native.status })
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('Update the StackOS desktop app')
    expect(button(wrapper, 'Enable backups').attributes('disabled')).toBeDefined()
    expect(button(wrapper, 'Restore backup').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('shows status errors and allows recovery without a configured installation', async () => {
    vi.mocked(loadBackupStatus).mockRejectedValueOnce(new Error('Local console unavailable'))
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Local console unavailable')
    expect(wrapper.find('.ui-skeleton').exists()).toBe(false)
    expect(wrapper.get('[aria-label="Backup status"]').text()).not.toContain(
      'Checking backup status',
    )
    expect(wrapper.get('[aria-label="Backup status"]').text()).toContain(
      'Backup status unavailable',
    )
    expect(wrapper.text()).not.toContain('Enable backups')
    expect(button(wrapper, 'Restore backup').attributes('disabled')).toBeUndefined()
    await button(wrapper, 'Refresh backups').trigger('click')
    await flushPromises()
    expect(button(wrapper, 'Enable backups').exists()).toBe(true)
  })

  it('shows completed recovery after a status error while retaining unavailable status', async () => {
    vi.mocked(loadBackupStatus).mockRejectedValueOnce(new Error('Earlier status read failed.'))
    native.restoreBackup.mockResolvedValue({
      ok: true,
      status: 'completed',
      message: 'Recovered successfully.',
      outputPath: '/chosen/recovered',
    })
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Restore backup').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="status"]').text()).toContain('Recovered successfully.')
    expect(wrapper.get('[role="status"]').text()).toContain('/chosen/recovered')
    expect(wrapper.get('h2').text()).toBe('Backup status unavailable')
    expect(wrapper.text()).toContain('Current backup status is unavailable.')
    expect(wrapper.text()).not.toContain('Earlier status read failed.')
    expect(loadBackupStatus).toHaveBeenCalledTimes(1)
    expect(configureBackups).not.toHaveBeenCalled()
  })

  it('retains a status error when recovery is cancelled', async () => {
    vi.mocked(loadBackupStatus).mockRejectedValueOnce(new Error('Earlier status read failed.'))
    native.restoreBackup.mockResolvedValue({
      ok: false,
      status: 'cancelled',
      message: 'Cancelled.',
    })
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Restore backup').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Earlier status read failed.')
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(loadBackupStatus).toHaveBeenCalledTimes(1)
  })

  it('preserves the primary button and restores focus when setup becomes configured', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValueOnce(fixture()).mockResolvedValue(configured())
    const wrapper = mountPanel({ attachTo: document.body })
    await flushPromises()
    const trigger = button(wrapper, 'Enable backups')
    const element = trigger.element as HTMLButtonElement
    element.focus()
    await trigger.trigger('click')
    await flushPromises()
    expect(button(wrapper, 'Back up now').element).toBe(element)
    expect(document.activeElement).toBe(element)
    expect(element.disabled).toBe(false)
  })

  it('does not steal focus when setup becomes configured while another control is selected', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValueOnce(fixture()).mockResolvedValue(configured())
    let finish!: (value: DesktopBackupResult) => void
    native.setupBackups.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const wrapper = mountPanel({ attachTo: document.body })
    await flushPromises()
    const trigger = button(wrapper, 'Enable backups')
    ;(trigger.element as HTMLButtonElement).focus()
    await trigger.trigger('click')
    const other = document.createElement('button')
    document.body.append(other)
    other.focus()
    finish({ ok: true, status: 'completed', message: 'Backups enabled.' })
    await flushPromises()
    expect(document.activeElement).toBe(other)
    expect(button(wrapper, 'Back up now').exists()).toBe(true)
    other.remove()
  })

  it.each(['completed', 'partial'] as const)(
    'keeps selected-image controls and their nodes through setup %s',
    async (resultStatus) => {
      vi.mocked(loadBackupStatus).mockResolvedValueOnce(fixture()).mockResolvedValue(configured())
      let finish!: (value: DesktopBackupResult) => void
      native.setupBackups.mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
      const wrapper = mountPanel()
      await flushPromises()
      const primary = button(wrapper, 'Enable backups').element
      const retained = [
        'Restore backup',
        'Refresh backups',
        'Export backup',
        'Export key',
        'Manage',
      ].map((label) => ({ label, element: button(wrapper, label).element }))
      const assertLayout = () => {
        expect(
          wrapper
            .get('.ui-page-header')
            .element.contains(button(wrapper, 'Restore backup').element),
        ).toBe(true)
        const band = wrapper.get('section[aria-label="Backup status"]')
        expect(band.element.contains(primary)).toBe(true)
        expect(band.element.contains(button(wrapper, 'Refresh backups').element)).toBe(true)
        const files = wrapper.get('section[aria-label="Recovery files"]')
        expect(files.element.contains(button(wrapper, 'Export backup').element)).toBe(true)
        expect(files.element.contains(button(wrapper, 'Export key').element)).toBe(true)
        expect(wrapper.find('.ui-card').exists()).toBe(false)
        expect(wrapper.find('details').exists()).toBe(false)
      }
      assertLayout()
      await button(wrapper, 'Enable backups').trigger('click')
      assertLayout()
      finish({ ok: resultStatus === 'completed', status: resultStatus, message: 'Setup result.' })
      await flushPromises()
      expect(button(wrapper, 'Back up now').element).toBe(primary)
      for (const item of retained) expect(button(wrapper, item.label).element).toBe(item.element)
      assertLayout()
    },
  )

  it('keeps feedback after the fixed page controls and shows the current cadence', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Backups')
    expect(wrapper.get('h2').text()).toBe('Automatic backups on')
    expect(wrapper.get('section[aria-label="Backup status"]').text()).toContain(
      'Every 6 hours · Keep 11 backups',
    )
    const feedback = wrapper.get('[data-test="backup-feedback"]').element
    for (const control of wrapper.findAll('button')) {
      expect(
        feedback.compareDocumentPosition(control.element) & Node.DOCUMENT_POSITION_PRECEDING,
      ).toBeTruthy()
    }
  })

  it.each([
    ['ArrowDown', 'Backup settings'],
    ['End', 'Included data and history'],
  ])('opens %s menu selection by keyboard and returns focus on Escape', async (key, title) => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    const wrapper = mountPanel({ attachTo: document.body })
    await flushPromises()
    const manageButton = button(wrapper, 'Manage')
    ;(manageButton.element as HTMLButtonElement).focus()
    await manageButton.trigger('click')
    expect(manageButton.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[role="menu"]').classes()).toEqual(
      expect.arrayContaining(['bottom-full', 'right-0']),
    )
    document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain(title)
    expect(manageButton.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(button(wrapper, 'Close panel').element)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(manageButton.element)
    expect(configureBackups).not.toHaveBeenCalled()
  })

  it.each(['completed', 'failed'] as const)(
    'keeps settings focus inside the panel after a save is %s',
    async (outcome) => {
      vi.mocked(loadBackupStatus).mockResolvedValue(configured())
      let finish!: (value: BackupStatus) => void
      let fail!: (reason: Error) => void
      vi.mocked(configureBackups).mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            finish = resolve
            fail = reject
          }),
      )
      const wrapper = mountPanel({ attachTo: document.body })
      await flushPromises()
      await chooseManage(wrapper, 'Backup settings')
      await wrapper.get('input[type="number"]').setValue('12')
      const saveButton = button(wrapper, 'Save backup settings').element as HTMLButtonElement
      saveButton.focus()
      wrapper
        .get('form')
        .element.dispatchEvent(
          new SubmitEvent('submit', { bubbles: true, cancelable: true, submitter: saveButton }),
        )
      await flushPromises()
      loseFocusToBody()
      if (outcome === 'completed') finish(configured({ interval_hours: 12 }))
      else fail(new Error('Could not save this schedule.'))
      await flushPromises()

      expect(wrapper.get('[role="dialog"]').element.contains(document.activeElement)).toBe(true)
      if (outcome === 'completed') {
        expect(saveButton.disabled).toBe(true)
        expect(document.activeElement).toBe(wrapper.get('input[type="checkbox"]').element)
        expect(wrapper.get('[role="dialog"]').text()).toContain('Backup settings saved.')
      } else {
        expect(document.activeElement).toBe(saveButton)
        expect(numberValues(wrapper)).toEqual(['12', '11'])
      }
    },
  )

  it('does not steal focus from another settings control after a successful save', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    let finish!: (value: BackupStatus) => void
    vi.mocked(configureBackups).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const wrapper = mountPanel({ attachTo: document.body })
    await flushPromises()
    await chooseManage(wrapper, 'Backup settings')
    await wrapper.get('input[type="number"]').setValue('12')
    ;(button(wrapper, 'Save backup settings').element as HTMLButtonElement).focus()
    await wrapper.get('form').trigger('submit')
    const close = button(wrapper, 'Close panel').element as HTMLButtonElement
    close.focus()
    finish(configured({ interval_hours: 12 }))
    await flushPromises()
    expect(document.activeElement).toBe(close)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
  })

  it('restores Manage focus when opening the backup folder finishes', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    let finish!: (value: DesktopBackupResult) => void
    native.openBackupFolder.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const wrapper = mountPanel({ attachTo: document.body })
    await flushPromises()
    const manageButton = button(wrapper, 'Manage').element
    await chooseManage(wrapper, 'Open backup folder')
    loseFocusToBody()
    finish({ ok: true, status: 'completed', message: 'Folder opened.' })
    await flushPromises()
    expect(document.activeElement).toBe(manageButton)
    expect(native.openBackupFolder).toHaveBeenCalledWith()
  })

  it.each(['completed', 'cancelled', 'failed'] as const)(
    'restores the initiating control after native %s',
    async (resultStatus) => {
      let finish!: (value: DesktopBackupResult) => void
      native.restoreBackup.mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
      const wrapper = mountPanel({ attachTo: document.body })
      await flushPromises()
      const trigger = button(wrapper, 'Restore backup')
      const element = trigger.element as HTMLButtonElement
      element.focus()
      await trigger.trigger('click')
      // Browsers drop focus when the initiating native button becomes disabled.
      loseFocusToBody()
      expect(document.activeElement).toBe(document.body)
      finish({ ok: resultStatus === 'completed', status: resultStatus, message: 'Safe result.' })
      await flushPromises()
      expect(document.activeElement).toBe(element)
      expect(element.disabled).toBe(false)
    },
  )

  it('restores focus after backup creation and status reload', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(configured())
    let finish!: () => void
    vi.mocked(createBackup).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const wrapper = mountPanel({ attachTo: document.body })
    await flushPromises()
    const trigger = button(wrapper, 'Back up now')
    const element = trigger.element as HTMLButtonElement
    element.focus()
    await trigger.trigger('click')
    loseFocusToBody()
    finish()
    await flushPromises()
    expect(document.activeElement).toBe(element)
    expect(loadBackupStatus).toHaveBeenCalledTimes(2)
  })

  it('does not steal focus when the user selects another control during an action', async () => {
    let finish!: (value: DesktopBackupResult) => void
    native.restoreBackup.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const wrapper = mountPanel({ attachTo: document.body })
    await flushPromises()
    const trigger = button(wrapper, 'Restore backup')
    ;(trigger.element as HTMLButtonElement).focus()
    await trigger.trigger('click')
    const other = document.createElement('button')
    document.body.append(other)
    other.focus()
    finish({ ok: false, status: 'cancelled', message: 'Cancelled.' })
    await flushPromises()
    expect(document.activeElement).toBe(other)
    other.remove()
  })

  it('retains the previous successful archive when a new backup fails', async () => {
    vi.mocked(loadBackupStatus).mockResolvedValue(
      configured({
        last_success: { created_at: '2026-10-13T10:00:00Z', path: '/custom/backups/good.sbak' },
      }),
    )
    vi.mocked(createBackup).mockRejectedValue(new Error('Writer is busy.'))
    const wrapper = mountPanel()
    await flushPromises()
    await button(wrapper, 'Back up now').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Writer is busy.')
    await chooseManage(wrapper, 'Included data and history')
    expect(wrapper.text()).toContain('good.sbak')
    expect(native.setupBackups).not.toHaveBeenCalled()
  })

  it('shows automatic project coverage and does not misreport failed inspection as absent files', async () => {
    vi.mocked(loadBackupStatus)
      .mockResolvedValueOnce(configured({ project_ids: [1, 2] }))
      .mockResolvedValue(configured({ project_ids: null }))
    const wrapper = mountPanel()
    await flushPromises()
    await chooseManage(wrapper, 'Included data and history')
    expect(wrapper.text()).toContain(
      'Project Finance records, invoices, receipts and reports are included automatically.',
    )
    expect(wrapper.text()).toContain('Covered project IDs: 1, 2.')
    await closePanel(wrapper)
    await button(wrapper, 'Refresh backups').trigger('click')
    await flushPromises()
    await chooseManage(wrapper, 'Included data and history')
    expect(wrapper.text()).toContain('Project coverage could not be verified.')
    expect(wrapper.text()).not.toContain('No managed project files are present yet.')
  })
})
