<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  UiButton,
  UiCallout,
  UiCard,
  UiCheckbox,
  UiFormField,
  UiInput,
  UiSectionHeader,
  UiSkeleton,
  UiTextarea,
} from '@/components/ui'
import { formatApiError } from '@/lib/client'
import { configureBackups, createBackup, loadBackupStatus, type BackupStatus } from '@/lib/backups'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'

const status = ref<BackupStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const confirmation = ref('')
const enabled = ref(false)
const interval = ref<string | number | null>(24)
const keepLast = ref<string | number | null>(30)
const publicKey = ref('')

function applyStatus(value: BackupStatus): void {
  status.value = value
  enabled.value = value.enabled
  interval.value = value.interval_hours
  keepLast.value = value.keep_last
}

async function refresh(): Promise<void> {
  loading.value = true
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
  error.value = ''
  confirmation.value = ''
  if (
    publicKey.value.trim() &&
    !/^-----BEGIN PUBLIC KEY-----\r?\n[A-Za-z0-9+/=\r\n]+\r?\n-----END PUBLIC KEY-----$/.test(
      publicKey.value.trim(),
    )
  ) {
    error.value =
      'Use only an X25519 PUBLIC KEY PEM. Keep the recovery private key in its local file.'
    return
  }
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
  saving.value = true
  try {
    applyStatus(
      await configureBackups({
        enabled: enabled.value,
        interval_hours: Number(interval.value),
        keep_last: Number(keepLast.value),
        ...(publicKey.value.trim() ? { recipient_public_key: publicKey.value.trim() } : {}),
      }),
    )
    publicKey.value = ''
    confirmation.value = 'Backup settings saved.'
  } catch (err) {
    error.value = formatApiError(err, 'Could not save backup settings.')
  } finally {
    saving.value = false
  }
}

async function backupNow(): Promise<void> {
  saving.value = true
  error.value = ''
  confirmation.value = ''
  try {
    await createBackup()
    applyStatus(await loadBackupStatus())
    confirmation.value = 'Encrypted backup created.'
  } catch (err) {
    error.value = formatApiError(err, 'Backup failed.')
    // Preserve the previous successful outcome; the service also records failures.
    try {
      applyStatus(await loadBackupStatus())
    } catch {
      /* Keep current metadata. */
    }
  } finally {
    saving.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <UiCard
    section
    aria-label="Local backups"
  >
    <UiSectionHeader
      title="Local backups"
      description="This StackOS install and explicitly enrolled folders across projects."
    >
      <template #actions>
        <UiButton
          variant="ghost"
          size="sm"
          :loading="loading"
          :disabled="saving"
          @click="refresh"
        >
          Refresh backups
        </UiButton>
      </template>
    </UiSectionHeader>
    <UiCallout
      v-if="error"
      tone="danger"
      class="mt-3"
      role="alert"
    >
      {{ error }}
    </UiCallout>
    <p
      v-if="confirmation"
      role="status"
      class="mt-3 text-sm text-fg-muted"
    >
      {{ confirmation }}
    </p>
    <UiSkeleton
      v-if="loading && !status"
      shape="block"
      height="8rem"
      class="mt-3"
    />
    <template v-else-if="status">
      <UiCallout
        v-if="status.last_error"
        tone="warning"
        class="mt-3"
      >
        Backup needs attention: {{ status.last_error.message }} {{ status.last_error.next_action }}
      </UiCallout>
      <p class="mt-3 text-sm text-fg-muted">
        Automatic backups are {{ status.enabled ? 'enabled' : 'off' }}.
        <span v-if="!status.configured">Set up a public recipient key before creating backups.</span>
        <span v-else-if="!status.ready">The current configuration needs attention before a backup can run.</span>
      </p>
      <p class="mt-2 text-xs text-fg-muted">
        Destination: <span class="break-all">{{ status.destination }}</span>
      </p>
      <p class="mt-2 text-xs text-fg-muted">
        Last successful backup:
        {{
          status.last_success?.created_at
            ? formatAbsoluteDateTime(status.last_success.created_at)
            : 'None recorded'
        }}
      </p>
      <p v-if="status.last_attempt?.started_at" class="mt-1 text-xs text-fg-muted">
        Last attempt: {{ formatAbsoluteDateTime(status.last_attempt.started_at) }}
        <span v-if="status.last_attempt.status"> · {{ status.last_attempt.status }}</span>
      </p>
      <p
        v-if="status.last_success?.path"
        class="mt-1 break-all text-xs text-fg-muted"
      >
        {{ status.last_success.path }}
      </p>
      <p
        v-if="status.recipient_fingerprint"
        class="mt-2 break-all text-2xs text-fg-subtle"
      >
        Recipient fingerprint: {{ status.recipient_fingerprint }}
      </p>
      <div class="mt-4 border-t border-subtle pt-3">
        <p class="text-sm font-medium text-fg-strong">
          Included external folders
        </p>
        <ul
          v-if="status.targets.length"
          class="mt-2 space-y-2 text-xs text-fg-muted"
        >
          <li
            v-for="target in status.targets"
            :key="target.id"
          >
            <span class="font-medium">{{ target.label }}</span> ·
            <span class="break-all">{{ target.path }}</span>
            <p
              v-if="target.lock_file"
              class="break-all text-2xs"
            >
              Writer lock: {{ target.lock_file }}
            </p>
          </li>
        </ul>
        <UiCallout
          v-else
          tone="warning"
          class="mt-2"
        >
          No external folders are enrolled. Finance bills are included only when the entire finance
          folder is enrolled.
        </UiCallout>
        <p class="mt-2 text-xs text-fg-muted">
          Enroll a folder with the local CLI. Existing folders remain included when settings are
          saved here.
        </p>
        <pre class="mt-2 overflow-x-auto rounded-sm bg-bg-sunken p-2 text-2xs">
stackos backups configure --target-id finance --target-path /absolute/finance --target-lock /absolute/finance/.writer.lock</pre>
      </div>
      <form
        class="mt-4 space-y-4 border-t border-subtle pt-3"
        @submit.prevent="save"
      >
        <UiFormField
          label="Recipient public key"
          help="Paste only the X25519 PUBLIC KEY PEM. Keep the independent recovery private file separately and off-device."
        >
          <UiTextarea
            v-model="publicKey"
            :rows="3"
            :disabled="saving"
            placeholder="-----BEGIN PUBLIC KEY-----"
          />
        </UiFormField>
        <p class="text-xs text-fg-muted">
          Generate the key pair locally:
          <code>stackos backups keygen --private-key-file /separate/recovery.pem --public-key-file
            /separate/recipient.pem</code>
        </p>
        <UiCheckbox
          v-model="enabled"
          :disabled="saving"
          label="Enable automatic encrypted backups"
          description="Opt in explicitly, then save. New installations start with this off."
        />
        <div class="grid gap-3 sm:grid-cols-2">
          <UiFormField label="Backup interval (hours)">
            <UiInput
              v-model="interval"
              type="number"
              min="1"
              max="168"
              :disabled="saving"
            />
          </UiFormField>
          <UiFormField label="Successful automatic backups to keep">
            <UiInput
              v-model="keepLast"
              type="number"
              min="1"
              max="365"
              :disabled="saving"
            />
          </UiFormField>
        </div>
        <div class="flex flex-wrap gap-2">
          <UiButton
            type="submit"
            :loading="saving"
            :disabled="loading"
          >
            Save backup settings
          </UiButton>
          <UiButton
            variant="secondary"
            :disabled="!status.ready || saving || loading"
            @click="backupNow"
          >
            Back up now
          </UiButton>
        </div>
      </form>
    </template>
  </UiCard>
</template>
