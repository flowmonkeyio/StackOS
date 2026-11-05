<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  UiButton,
  UiCallout,
  UiCheckbox,
  UiDropdownMenu,
  UiFormField,
  UiIcon,
  UiIconButton,
  UiInput,
  UiPageHeader,
  UiSidePanel,
  UiSkeleton,
} from '@/components/ui'
import { desktop, type DesktopBackupAction, type DesktopBackupResult } from '@/lib/desktop'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'

import { formatApiError } from '@/lib/client'
import { configureBackups, createBackup, loadBackupStatus, type BackupStatus } from '@/lib/backups'
const status = ref<BackupStatus | null>(null)
const loading = ref(true)
const current = ref(false)
const busy = ref<DesktopBackupAction | 'save' | 'backup' | null>(null)
const error = ref('')
const notice = ref<DesktopBackupResult | null>(null)
const activePanel = ref<'settings' | 'history' | null>(null)
const manageTrigger = ref<InstanceType<typeof UiButton> | null>(null)
const enabled = ref(false)
const interval = ref<string | number | null>(null)
const keepLast = ref<string | number | null>(null)
const disabled = computed(() => loading.value || busy.value !== null)
const changed = computed(
  () =>
    status.value &&
    (enabled.value !== status.value.enabled ||
      Number(interval.value) !== status.value.interval_hours ||
      Number(keepLast.value) !== status.value.keep_last),
)
function applyStatus(value: BackupStatus): void {
  status.value = value
  enabled.value = value.enabled
  interval.value = value.interval_hours
  keepLast.value = value.keep_last
  current.value = true
}

async function refresh(): Promise<void> {
  loading.value = true
  current.value = false
  error.value = ''
  try {
    applyStatus(await loadBackupStatus())
  } catch (err) {
    error.value = formatApiError(err, 'Could not load backup status.')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (disabled.value || !current.value || !status.value || !changed.value) return
  error.value = ''
  notice.value = null
  if (
    !Number.isInteger(Number(interval.value)) ||
    Number(interval.value) < 1 ||
    Number(interval.value) > 168 ||
    !Number.isInteger(Number(keepLast.value)) ||
    Number(keepLast.value) < 1 ||
    Number(keepLast.value) > 365
  ) {
    error.value =
      'Use an interval of 1–168 hours and retention of 1–365 successful automatic backups.'
    return
  }
  busy.value = 'save'
  try {
    applyStatus(
      await configureBackups({
        ...(enabled.value !== status.value.enabled ? { enabled: enabled.value } : {}),
        ...(Number(interval.value) !== status.value.interval_hours
          ? { interval_hours: Number(interval.value) }
          : {}),
        ...(Number(keepLast.value) !== status.value.keep_last
          ? { keep_last: Number(keepLast.value) }
          : {}),
      }),
    )
    notice.value = { ok: true, status: 'completed', message: 'Backup settings saved.' }
  } catch (err) {
    error.value = formatApiError(err, 'Could not save backup settings.')
  } finally {
    busy.value = null
  }
}

async function backupNow(): Promise<void> {
  if (disabled.value || !current.value || !status.value?.ready) return
  busy.value = 'backup'
  error.value = ''
  notice.value = null
  try {
    await createBackup()
    notice.value = { ok: true, status: 'completed', message: 'Encrypted backup created.' }
  } catch (err) {
    notice.value = { ok: false, status: 'failed', message: formatApiError(err, 'Backup failed.') }
  } finally {
    await refresh()
    busy.value = null
  }
}

async function runNative(action: DesktopBackupAction): Promise<void> {
  if (disabled.value || !desktop.hasBackupAction(action)) return
  if (action === 'setupBackups' && (!current.value || status.value?.configured)) return
  busy.value = action
  try {
    const result = await desktop[action]()
    if (result?.status === 'cancelled') return
    error.value = ''
    notice.value = result ?? {
      ok: false,
      status: 'failed',
      message: 'The desktop action did not finish. Check the desktop app and try again.',
    }
    // Setup can succeed even when its first backup fails. Reload the policy so
    // retry uses ordinary backup creation and never generates another key.
    if (action === 'setupBackups') await refresh()
  } finally {
    busy.value = null
  }
}

onMounted(refresh)

const heading = computed(() => {
  if (!status.value) return loading.value ? 'Loading backups' : 'Backup status unavailable'
  if (!status.value.configured) return 'Set up backups'
  return status.value.enabled ? 'Automatic backups on' : 'Automatic backups paused'
})
const lastBackup = computed(() =>
  !status.value
    ? loading.value
      ? 'Checking backup history…'
      : 'Backup history unavailable'
    : status.value.last_success?.created_at
      ? formatAbsoluteDateTime(status.value.last_success.created_at)
      : 'No successful backup yet',
)
const cadence = computed(() =>
  status.value
    ? `Every ${status.value.interval_hours} ${status.value.interval_hours === 1 ? 'hour' : 'hours'} · Keep ${status.value.keep_last} backups`
    : 'Your existing settings will be preserved.',
)
const nativeAvailable = (
  [
    'setupBackups',
    'openBackupFolder',
    'exportLatestBackup',
    'exportRecoveryKey',
    'restoreBackup',
  ] as const
).every((action) => desktop.hasBackupAction(action))
const nativeGuidance = desktop.isShell()
  ? 'Update the StackOS desktop app to set up backups, export files, or restore.'
  : 'Open the StackOS desktop app to set up backups, export files, or restore.'
const manageItems = computed(() => [
  {
    key: 'folder',
    label: 'Open backup folder',
    icon: 'folder',
    disabled: disabled.value || !desktop.hasBackupAction('openBackupFolder'),
  },
  {
    key: 'settings',
    label: 'Backup settings',
    icon: 'settings',
    disabled: disabled.value || !current.value,
  },
  {
    key: 'history',
    label: 'Included data and history',
    icon: 'file-text',
    disabled: disabled.value,
  },
])
async function manage(item: { key: string }): Promise<void> {
  if (item.key === 'folder') {
    const trigger = manageTrigger.value?.$el
    if (trigger instanceof HTMLElement)
      await withActionFocus(trigger, () => runNative('openBackupFolder'))
  } else if (item.key === 'settings' || item.key === 'history') {
    if (item.key === 'settings') {
      error.value = ''
      notice.value = null
    }
    activePanel.value = item.key
  }
}

async function withActionFocus(
  event: Event | HTMLElement,
  action: () => Promise<void>,
): Promise<void> {
  const source =
    event instanceof HTMLElement
      ? event
      : event.type === 'submit'
        ? ((event as SubmitEvent).submitter ?? document.activeElement)
        : event.currentTarget
  const control = source instanceof HTMLElement ? source : null
  try {
    await action()
  } finally {
    await nextTick()
    if (
      control?.isConnected &&
      (document.activeElement === document.body || document.activeElement === control)
    ) {
      // A successful save disables its unchanged form action. Keep keyboard
      // focus on an enabled setting instead of leaving it outside the panel.
      const target = control.matches(':disabled')
        ? control.closest('form')?.querySelector<HTMLElement>('input:not(:disabled)')
        : control
      target?.focus()
    }
  }
}
</script>

<template>
  <section
    aria-label="Local backups"
    :aria-busy="disabled"
    class="space-y-5"
  >
    <UiPageHeader
      title="Backups"
      description="Automatic backups for this installation."
    >
      <template #actions>
        <UiSkeleton
          v-if="!status && loading"
          shape="block"
          width="8rem"
          height="2rem"
        />
        <UiButton
          v-else
          :loading="busy === 'restoreBackup'"
          :disabled="disabled || !desktop.hasBackupAction('restoreBackup')"
          @click="withActionFocus($event, () => runNative('restoreBackup'))"
        >
          Restore backup
        </UiButton>
      </template>
    </UiPageHeader>

    <section
      aria-label="Backup status"
      class="grid grid-cols-1 gap-3 rounded-lg px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto]"
      :class="
        status?.configured && status.enabled && status.ready
          ? 'bg-accent-subtle'
          : 'bg-bg-surface-alt'
      "
    >
      <div class="order-2 flex min-w-0 items-start gap-3 sm:order-1">
        <UiIcon
          :name="
            !status
              ? 'circle-dashed'
              : !status.configured
                ? 'archive'
                : status.enabled
                  ? 'circle-check'
                  : 'pause'
          "
          class="mt-0.5 h-4 w-4 shrink-0"
          :class="
            status?.configured && status.enabled && status.ready ? 'text-accent' : 'text-fg-muted'
          "
        />
        <div class="min-w-0 space-y-1">
          <h2 class="t-h3 truncate text-fg-strong">
            {{ heading }}
          </h2>
          <p
            class="truncate text-sm text-fg-default"
            :title="status?.configured ? lastBackup : undefined"
          >
            {{
              status?.configured
                ? 'Last backup: ' + lastBackup
                : status
                  ? 'Choose a backup folder.'
                  : loading
                    ? 'Checking backup status…'
                    : 'Refresh to check backup status.'
            }}
          </p>
          <p
            class="truncate text-xs text-fg-muted"
            :title="status?.configured ? cadence : undefined"
          >
            {{
              status?.configured
                ? cadence
                : status
                  ? 'Then save your recovery key.'
                  : 'Your existing settings will be preserved.'
            }}
          </p>
        </div>
      </div>
      <div class="order-1 space-y-2 self-start sm:order-2">
        <div class="flex items-center gap-2">
          <UiSkeleton
            v-if="!status && loading"
            shape="block"
            width="8rem"
            height="2rem"
          />
          <span
            v-else-if="!status"
            class="h-8 w-32"
            aria-hidden="true"
          />
          <UiButton
            v-else
            class="min-w-32"
            variant="primary"
            :loading="busy === 'setupBackups' || busy === 'backup'"
            :disabled="
              disabled ||
                !current ||
                (status.configured ? !status.ready : !desktop.hasBackupAction('setupBackups'))
            "
            @click="
              withActionFocus(
                $event,
                status.configured ? backupNow : () => runNative('setupBackups'),
              )
            "
          >
            {{ status.configured ? 'Back up now' : 'Enable backups' }}
          </UiButton>
          <UiIconButton
            aria-label="Refresh backups"
            :loading="loading"
            :disabled="disabled"
            @click="withActionFocus($event, refresh)"
          >
            <UiIcon name="refresh" />
          </UiIconButton>
        </div>
        <p class="text-xs text-fg-muted">
          {{
            loading
              ? 'Checking backup status…'
              : busy
                ? 'Working…'
                : !current
                  ? 'Status unavailable'
                  : !status?.configured
                    ? 'Set up in the desktop app'
                    : status.ready
                      ? 'Ready for the next backup'
                      : 'Backup needs attention'
          }}
        </p>
      </div>
    </section>

    <section aria-label="Recovery files">
      <h2 class="t-h3 text-fg-strong">
        Keep these two files safe
      </h2>
      <p class="mt-1 text-sm text-fg-muted">
        You need both to recover on another device.
      </p>
      <div class="mt-3 divide-y divide-border-subtle">
        <div class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 py-4">
          <UiIcon
            name="file-text"
            class="h-5 w-5 text-fg-muted"
          />
          <div class="min-w-0">
            <h3 class="truncate text-sm font-medium text-fg-strong">
              Latest backup
            </h3>
            <p
              class="mt-1 truncate text-xs text-fg-muted"
              :title="lastBackup"
            >
              {{ lastBackup }}
            </p>
          </div>
          <UiButton
            class="min-w-28"
            :loading="busy === 'exportLatestBackup'"
            :disabled="
              disabled ||
                !status?.configured ||
                !status.last_success ||
                !desktop.hasBackupAction('exportLatestBackup')
            "
            @click="withActionFocus($event, () => runNative('exportLatestBackup'))"
          >
            Export backup
          </UiButton>
        </div>
        <div class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 py-4">
          <UiIcon
            name="key"
            class="h-5 w-5 text-fg-muted"
          />
          <div class="min-w-0">
            <h3 class="truncate text-sm font-medium text-fg-strong">
              Recovery key
            </h3>
            <p
              class="mt-1 truncate text-xs text-fg-muted"
              title="Required to open your encrypted backup."
            >
              Required to open your encrypted backup.
            </p>
          </div>
          <UiButton
            class="min-w-28"
            :loading="busy === 'exportRecoveryKey'"
            :disabled="
              disabled || !status?.configured || !desktop.hasBackupAction('exportRecoveryKey')
            "
            @click="withActionFocus($event, () => runNative('exportRecoveryKey'))"
          >
            Export key
          </UiButton>
        </div>
      </div>
      <p class="mt-3 text-xs text-fg-muted">
        Keep a backup and your recovery key somewhere you can access if this device is lost.
      </p>
    </section>

    <div class="flex items-start justify-between gap-3 border-t border-subtle pt-4">
      <div class="flex min-w-0 items-start gap-3">
        <UiIcon
          name="folder"
          class="mt-0.5 h-5 w-5 shrink-0 text-fg-muted"
        />
        <p class="break-all text-sm text-fg-default">
          {{
            !status
              ? loading
                ? 'Checking backup folder…'
                : 'Backup folder unavailable'
              : status.configured
                ? status.destination
                : 'Choose a backup folder during setup.'
          }}
        </p>
      </div>
      <UiDropdownMenu
        :items="manageItems"
        placement="top-end"
        aria-label="Manage backups"
        @select="manage"
      >
        <template #trigger="{ toggle, open }">
          <UiButton
            ref="manageTrigger"
            data-dropdown-trigger
            icon-right="chevron-down"
            aria-haspopup="menu"
            :aria-expanded="open"
            :disabled="disabled || !status?.configured"
            @click="toggle"
          >
            Manage
          </UiButton>
        </template>
      </UiDropdownMenu>
    </div>
    <p
      v-if="!nativeAvailable"
      class="text-sm text-fg-muted"
    >
      {{ nativeGuidance }}
    </p>
    <div
      class="min-h-10"
      data-test="backup-feedback"
    >
      <p
        v-if="busy || (loading && status)"
        role="status"
        class="text-sm text-fg-muted"
      >
        {{
          busy === 'save'
            ? 'Saving backup settings…'
            : busy === 'backup'
              ? 'Creating encrypted backup…'
              : busy
                ? 'Follow the prompts in the desktop window…'
                : 'Refreshing backup status…'
        }}
      </p>
      <UiCallout
        v-else-if="error"
        tone="danger"
        role="alert"
      >
        {{ error }}
      </UiCallout>
      <UiCallout
        v-else-if="notice && notice.status !== 'cancelled'"
        :tone="
          notice.status === 'failed'
            ? 'danger'
            : notice.status === 'partial'
              ? 'warning'
              : 'success'
        "
        :role="notice.status === 'failed' ? 'alert' : 'status'"
      >
        {{ notice.message }}
        <p
          v-if="notice.outputPath"
          class="mt-1 break-all text-xs"
        >
          {{ notice.outputPath }}
        </p>
      </UiCallout>
      <UiCallout
        v-else-if="status?.last_error"
        tone="warning"
      >
        {{ status.last_error.message }} {{ status.last_error.next_action }}
      </UiCallout>
      <p
        v-else-if="status?.configured && !status.ready"
        class="text-sm text-fg-muted"
      >
        The current configuration needs attention before a backup can run.
      </p>
      <p
        v-if="!loading && !busy && !current && !error"
        class="mt-2 text-sm text-fg-muted"
      >
        Current backup status is unavailable. Refresh to check it.
      </p>
    </div>

    <UiSidePanel
      :model-value="activePanel === 'settings'"
      title="Backup settings"
      @update:model-value="activePanel = $event ? 'settings' : null"
    >
      <form
        v-if="status"
        class="space-y-4"
        @submit.prevent="withActionFocus($event, save)"
      >
        <UiCheckbox
          v-model="enabled"
          :disabled="disabled || !current"
          label="Enable automatic encrypted backups"
          description="Changes take effect when you save."
        />
        <UiFormField label="Backup interval (hours)">
          <UiInput
            v-model="interval"
            type="number"
            min="1"
            max="168"
            :disabled="disabled || !current"
          />
        </UiFormField>
        <UiFormField label="Successful automatic backups to keep">
          <UiInput
            v-model="keepLast"
            type="number"
            min="1"
            max="365"
            :disabled="disabled || !current"
          />
        </UiFormField>
        <UiButton
          type="submit"
          :loading="busy === 'save'"
          :disabled="disabled || !current || !changed"
        >
          Save backup settings
        </UiButton>
        <UiCallout
          v-if="error"
          tone="danger"
          role="alert"
        >
          {{ error }}
        </UiCallout>
        <p
          v-else-if="notice"
          role="status"
          class="text-sm text-fg-muted"
        >
          {{ notice.message }}
        </p>
      </form>
    </UiSidePanel>
    <UiSidePanel
      :model-value="activePanel === 'history'"
      title="Included data and history"
      description="Restore recovers into a separate folder and leaves this installation unchanged."
      @update:model-value="activePanel = $event ? 'history' : null"
    >
      <div
        v-if="status"
        class="space-y-4"
      >
        <p class="text-sm text-fg-muted">
          Project Finance records, invoices, receipts and reports are included automatically. Each
          project is stored and recovered separately.
          <span v-if="status.project_ids?.length">Covered project IDs: {{ status.project_ids.join(', ') }}.</span>
          <span v-else-if="status.project_ids">No managed project files are present yet.</span>
          <span v-else>Project coverage could not be verified. Resolve the backup error before relying on
            coverage.</span>
        </p>
        <div>
          <h3 class="t-h3 text-fg-strong">
            Included external folders
          </h3>
          <ul
            v-if="status.targets.length"
            class="mt-2 space-y-2 text-sm text-fg-muted"
          >
            <li
              v-for="target in status.targets"
              :key="target.id"
            >
              {{ target.label }} · <span class="break-all">{{ target.path }}</span>
            </li>
          </ul>
          <p
            v-else
            class="mt-2 text-sm text-fg-muted"
          >
            No additional external folders are enrolled.
          </p>
        </div>
        <p
          v-if="status.last_attempt?.started_at"
          class="text-sm text-fg-muted"
        >
          Last attempt: {{ formatAbsoluteDateTime(status.last_attempt.started_at) }}
          <span v-if="status.last_attempt.status"> · {{ status.last_attempt.status }}</span>
        </p>
        <p
          v-if="status.last_success?.path"
          class="break-all text-sm text-fg-muted"
        >
          {{ status.last_success.path }}
        </p>
        <p
          v-if="status.recipient_fingerprint"
          class="break-all text-xs text-fg-subtle"
        >
          Recovery key fingerprint: {{ status.recipient_fingerprint }}
        </p>
      </div>
    </UiSidePanel>
  </section>
</template>
