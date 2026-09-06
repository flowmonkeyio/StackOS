<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import ProviderMark from '@/components/domain/ProviderMark.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiConfirmDialog,
  UiEmptyState,
  UiFilterBar,
  UiPageHeader,
  UiPageShell,
  UiSkeleton,
} from '@/components/ui'
import { useProjectsStore } from '@/stores/projects'
import AddAccountPanel from './accounts/AddAccountPanel.vue'
import {
  useAccountCredentials,
  type AccountRow,
  type CommunicationAccountUse,
} from './accounts/useAccountCredentials'
import {
  accountLabel,
  connectionActionKey,
  connectionNeedsAttention,
  connectionStatusKey,
  formatAuthType,
  providerLabel,
} from './connections/formatters'
import type { OAuthReturnStatus } from './connections/types'
import { credentialVerificationMessage } from './connections/credentialPresentation'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'

const route = useRoute()
const router = useRouter()
const noProject = computed<number | null>(() => null)
const projectsStore = useProjectsStore()
const { items: projects } = storeToRefs(projectsStore)
const initialLoadComplete = ref(false)
const search = ref('')

const {
  error,
  panelOpen,
  busyAction,
  providerMessages,
  accountMessages,
  oauthReturnMessage,
  fieldErrors,
  pendingRevoke,
  editing,
  editingSecretPresent,
  authMethods,
  selectedMethodKey,
  selectedMethod,
  setSelectedMethod,
  supportsCredential,
  inputType,
  isSecretField,
  methodFields,
  fieldOptions,
  hasFieldOptions,
  fieldValue,
  setFieldValue,
  displayNameValue,
  setDisplayNameValue,
  setSelectedProvider,
  accounts,
  communicationAccountUses,
  visibleAuthProviders,
  providerOptions,
  selectedProvider,
  load: loadAccounts,
  openAddAccount,
  openEditAccount,
  saveAccount,
  startProvider: startProviderAction,
  testAccount,
  requestRevoke,
  confirmRevoke,
  applyOAuthReturn,
} = useAccountCredentials(noProject)

const providerByKey = computed(
  () => new Map(visibleAuthProviders.value.map((provider) => [provider.key, provider])),
)
const accountGroups = computed(() => {
  const grouped = new Map<string, AccountRow[]>()
  for (const account of accounts.value) {
    const rows = grouped.get(account.provider_key) ?? []
    rows.push(account)
    grouped.set(account.provider_key, rows)
  }
  return Array.from(grouped, ([providerKey, rows]) => ({
    providerKey,
    provider: providerByKey.value.get(providerKey),
    accounts: rows.sort((left, right) => left.display_name.localeCompare(right.display_name)),
  })).sort((left, right) =>
    (left.provider?.name ?? providerLabel(left.providerKey)).localeCompare(
      right.provider?.name ?? providerLabel(right.providerKey),
    ),
  )
})
const visibleAccountGroups = computed(() => {
  const query = search.value.trim().toLowerCase()
  if (!query) return accountGroups.value
  return accountGroups.value
    .map((group) => ({
      ...group,
      accounts: group.accounts.filter((account) => {
        const haystack = [
          group.provider?.name ?? providerLabel(group.providerKey),
          group.provider?.description ?? '',
          account.display_name,
          safeAccountIdentity(account) ?? '',
          account.credential_ref,
          ...account.project_ids.map(projectName),
          ...accountUses(account).map((usage) => usage.profile_display_name),
        ]
          .join(' ')
          .toLowerCase()
        return haystack.includes(query)
      }),
    }))
    .filter((group) => group.accounts.length > 0)
})

function projectName(projectId: number): string {
  return projects.value.find((project) => project.id === projectId)?.name ?? `Project ${projectId}`
}

async function load(): Promise<void> {
  initialLoadComplete.value = false
  try {
    await Promise.all([loadAccounts(), projectsStore.refresh()])
    handleOAuthReturn()
    const accountRef = typeof route.query.account === 'string' ? route.query.account : ''
    const account = accounts.value.find((item) => item.credential_ref === accountRef)
    if (account) await openEditAccount(account)
  } finally {
    initialLoadComplete.value = true
  }
}

function handleOAuthReturn(): void {
  const rawStatus = route.query.oauth_status
  const statuses = new Set<OAuthReturnStatus>([
    'connected',
    'denied',
    'expired',
    'repair-required',
    'error',
  ])
  const status =
    rawStatus === 'stale-attempt'
      ? 'expired'
      : typeof rawStatus === 'string' && statuses.has(rawStatus as OAuthReturnStatus)
        ? (rawStatus as OAuthReturnStatus)
        : null
  if (!status) return
  applyOAuthReturn(
    status,
    typeof route.query.provider_key === 'string' ? route.query.provider_key : null,
  )
  const query = { ...route.query }
  delete query.oauth_status
  delete query.provider_key
  void router.replace({ query })
}

async function startProvider(...args: Parameters<typeof startProviderAction>): Promise<void> {
  const authorizationUrl = await startProviderAction(...args)
  if (authorizationUrl) window.open(authorizationUrl, '_self', 'noopener,noreferrer')
}

function closePanel(open: boolean): void {
  panelOpen.value = open
  if (!open && route.query.account) {
    const query = { ...route.query }
    delete query.account
    void router.replace({ query })
  }
}

function safeAccountIdentity(account: AccountRow): string | null {
  const identity = accountLabel(account)
  return identity !== '-' && identity !== account.display_name ? identity : null
}

function groupProjectCount(rows: AccountRow[]): number {
  return new Set(rows.flatMap((account) => account.project_ids)).size
}

function accountUses(account: AccountRow): CommunicationAccountUse[] {
  return communicationAccountUses.value.filter(
    (usage) => usage.credential_ref === account.credential_ref,
  )
}

function accountAttention(account: AccountRow): CommunicationAccountUse[] {
  return accountUses(account).filter((usage) => usage.attention_required)
}

function accountRepairs(account: AccountRow): CommunicationAccountUse[] {
  return accountUses(account).filter(
    (usage) => usage.profile_enabled && usage.binding_state !== 'ready',
  )
}

onMounted(load)
</script>

<template>
  <UiPageShell>
    <UiPageHeader
      title="Accounts"
      description="Set up provider Accounts once, then reuse them across projects."
    >
      <template #actions>
        <UiButton variant="primary" size="sm" icon-left="plus" @click="openAddAccount()">
          Add Account
        </UiButton>
      </template>
    </UiPageHeader>

    <UiCallout v-if="error" tone="danger">
      {{ error }}
    </UiCallout>
    <UiCallout v-if="oauthReturnMessage" :tone="oauthReturnMessage.tone">
      {{ oauthReturnMessage.text }}
    </UiCallout>

    <div v-if="!initialLoadComplete" class="grid gap-3" aria-label="Loading Accounts">
      <UiCard v-for="index in 3" :key="index">
        <UiSkeleton shape="line" :lines="3" />
      </UiCard>
    </div>

    <UiEmptyState
      v-else-if="error && accounts.length === 0"
      title="Accounts unavailable"
      description="StackOS could not load the Account inventory. Retry before creating or changing an Account."
      icon="alert-triangle"
      framed
    >
      <template #actions>
        <UiButton variant="secondary" size="sm" icon-left="refresh-cw" @click="load">
          Retry
        </UiButton>
      </template>
    </UiEmptyState>

    <UiEmptyState
      v-else-if="accounts.length === 0"
      title="No Accounts yet"
      description="Create a provider Account here. When setting up a project Connection, you can select it instead of entering credentials again."
      icon="key"
      framed
    >
      <template #actions>
        <UiButton variant="primary" size="sm" icon-left="plus" @click="openAddAccount()">
          Add Account
        </UiButton>
      </template>
    </UiEmptyState>

    <template v-else>
      <div class="sticky top-14 z-sticky -mx-1 bg-bg-app px-1 py-2 md:top-0">
        <UiFilterBar
          v-model:search="search"
          search-placeholder="Find an Account, provider, or project…"
          aria-label="Account filters"
        />
      </div>

      <UiEmptyState
        v-if="visibleAccountGroups.length === 0"
        title="No Accounts match"
        description="Try a different Account, provider, or project name."
        icon="search"
        framed
      />

      <div v-else class="grid gap-4">
        <section
          v-for="group in visibleAccountGroups"
          :key="group.providerKey"
          class="min-w-0"
          :aria-labelledby="`account-provider-${group.providerKey}`"
        >
          <UiCard :padded="false" class="min-w-0 overflow-hidden">
            <template #header>
              <div class="flex min-w-0 items-center gap-3">
                <ProviderMark
                  :name="group.provider?.name ?? providerLabel(group.providerKey)"
                  :provider-key="group.providerKey"
                  :plugin-slug="group.provider?.plugin_slug"
                />
                <div class="min-w-0">
                  <div class="flex min-w-0 flex-wrap items-center gap-2">
                    <h2
                      :id="`account-provider-${group.providerKey}`"
                      class="t-h3 truncate text-fg-strong"
                    >
                      {{ group.provider?.name ?? providerLabel(group.providerKey) }}
                    </h2>
                    <UiBadge variant="outline">
                      {{ group.accounts.length }}
                      {{ group.accounts.length === 1 ? 'Account' : 'Accounts' }}
                    </UiBadge>
                    <UiBadge v-if="groupProjectCount(group.accounts) > 0">
                      {{ groupProjectCount(group.accounts) }}
                      {{ groupProjectCount(group.accounts) === 1 ? 'project' : 'projects' }}
                    </UiBadge>
                  </div>
                  <p
                    v-if="group.provider?.description"
                    class="mt-0.5 truncate text-xs text-fg-subtle"
                  >
                    {{ group.provider.description }}
                  </p>
                </div>
              </div>
            </template>

            <ul
              class="divide-y divide-border-subtle"
              :aria-label="`${group.provider?.name ?? providerLabel(group.providerKey)} Accounts`"
            >
              <li
                v-for="account in group.accounts"
                :key="account.credential_ref"
                class="px-4 py-3"
                :class="connectionNeedsAttention(account) ? 'bg-warning-subtle' : ''"
              >
                <div class="flex flex-wrap items-start gap-x-4 gap-y-2">
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <h3 class="truncate text-sm font-semibold text-fg-strong">
                        {{ account.display_name }}
                      </h3>
                      <StatusBadge domain="connection" :status="connectionStatusKey(account)" />
                    </div>
                    <p class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-fg-muted">
                      <span>{{ formatAuthType(account.auth_type) }}</span>
                      <span aria-hidden="true" class="text-fg-subtle">·</span>
                      <span :title="formatAbsoluteDateTime(account.last_tested_at)">
                        {{
                          account.last_tested_at
                            ? `tested ${formatRelativeDateTime(account.last_tested_at)}`
                            : 'never tested'
                        }}
                      </span>
                      <template v-if="account.expires_at">
                        <span aria-hidden="true" class="text-fg-subtle">·</span>
                        <span :title="formatAbsoluteDateTime(account.expires_at)">
                          expires {{ formatRelativeDateTime(account.expires_at) }}
                        </span>
                      </template>
                      <template v-if="safeAccountIdentity(account)">
                        <span aria-hidden="true" class="text-fg-subtle">·</span>
                        <span class="truncate">{{ safeAccountIdentity(account) }}</span>
                      </template>
                    </p>
                  </div>

                  <div class="flex shrink-0 items-center gap-1">
                    <UiButton
                      size="sm"
                      variant="ghost"
                      :loading="busyAction === connectionActionKey(account.credential_ref, 'edit')"
                      @click="openEditAccount(account)"
                    >
                      Edit
                    </UiButton>
                    <UiButton
                      size="sm"
                      variant="secondary"
                      icon-left="bolt"
                      :loading="busyAction === connectionActionKey(account.credential_ref, 'test')"
                      @click="testAccount(account)"
                    >
                      Test
                    </UiButton>
                    <UiButton
                      size="sm"
                      variant="danger-ghost"
                      :disabled="account.project_ids.length > 0"
                      :title="
                        account.project_ids.length > 0
                          ? 'Detach this Account from every project before revoking it.'
                          : undefined
                      "
                      @click="requestRevoke(account)"
                    >
                      Revoke
                    </UiButton>
                  </div>
                </div>

                <div class="mt-2 flex min-w-0 flex-wrap items-center justify-between gap-2">
                  <div class="flex min-w-0 flex-wrap items-center gap-1.5">
                    <span class="text-xs text-fg-subtle">
                      {{ account.project_ids.length === 0 ? 'Not attached' : 'Attached to' }}
                    </span>
                    <RouterLink
                      v-for="projectId in account.project_ids"
                      :key="projectId"
                      :to="`/projects/${projectId}/connections`"
                      class="focus-ring rounded-full border border-border-subtle px-2 py-0.5 text-2xs text-fg-muted transition-colors hover:border-border-default hover:text-fg-strong"
                    >
                      {{ projectName(projectId) }}
                    </RouterLink>
                  </div>
                  <span
                    class="max-w-full truncate font-mono text-2xs text-fg-subtle"
                    :title="account.credential_ref"
                  >
                    {{ account.credential_ref }}
                  </span>
                </div>

                <div
                  v-if="accountUses(account).length > 0"
                  class="mt-2 flex min-w-0 flex-wrap items-center gap-1.5"
                >
                  <span class="text-xs text-fg-subtle">Communication profiles</span>
                  <RouterLink
                    v-for="usage in accountUses(account)"
                    :key="`${usage.project_id}:${usage.profile_ref}:${usage.provider_key}`"
                    :to="`/projects/${usage.project_id}/connections?section=bots`"
                    class="focus-ring rounded-full border border-border-subtle bg-bg-surface-alt px-2 py-0.5 text-2xs text-fg-muted transition-colors hover:border-border-default hover:text-fg-strong"
                  >
                    {{ usage.profile_display_name }} · {{ projectName(usage.project_id) }}
                  </RouterLink>
                </div>

                <UiCallout
                  v-for="usage in accountRepairs(account)"
                  :key="`repair:${usage.project_id}:${usage.profile_ref}:${usage.provider_key}`"
                  tone="warning"
                  density="compact"
                  class="mt-2"
                  :title="`${usage.profile_display_name} needs its Account binding repaired`"
                >
                  {{ usage.repair_message }}
                  <template #actions>
                    <UiButton
                      size="sm"
                      variant="secondary"
                      @click="router.push(`/projects/${usage.project_id}/connections?section=bots`)"
                    >
                      Repair profile
                    </UiButton>
                  </template>
                </UiCallout>

                <UiCallout
                  v-for="usage in accountAttention(account)"
                  :key="`attention:${usage.project_id}:${usage.profile_ref}`"
                  tone="warning"
                  density="compact"
                  class="mt-2"
                  :title="`${usage.profile_display_name} needs a Slack webhook update`"
                >
                  {{ usage.attention_message }}
                  <template #actions>
                    <UiButton
                      size="sm"
                      variant="secondary"
                      @click="
                        router.push(
                          `/projects/${usage.project_id}/connections?section=connectivity`,
                        )
                      "
                    >
                      Review connectivity
                    </UiButton>
                  </template>
                </UiCallout>

                <UiCallout
                  v-if="account.last_test?.ok === false"
                  tone="danger"
                  title="Verification failed"
                  density="compact"
                  class="mt-2"
                >
                  {{ credentialVerificationMessage(account.last_test) }}
                </UiCallout>

                <UiCallout
                  v-if="accountMessages[account.credential_ref]"
                  :tone="accountMessages[account.credential_ref].tone"
                  density="compact"
                  class="mt-2"
                >
                  {{ accountMessages[account.credential_ref].text }}
                </UiCallout>
              </li>
            </ul>
          </UiCard>
        </section>
      </div>
    </template>

    <AddAccountPanel
      :model-value="panelOpen"
      :selected-provider="selectedProvider"
      :visible-auth-providers="visibleAuthProviders"
      :provider-options="providerOptions"
      :provider-messages="providerMessages"
      :field-errors="fieldErrors"
      :busy-action="busyAction"
      :editing="editing"
      :secret-present="editingSecretPresent"
      :auth-methods="authMethods"
      :selected-method-key="selectedMethodKey"
      :selected-method="selectedMethod"
      :supports-credential="supportsCredential"
      :input-type="inputType"
      :is-secret-field="isSecretField"
      :method-fields="methodFields"
      :has-field-options="hasFieldOptions"
      :field-options="fieldOptions"
      :display-name-value="displayNameValue"
      :set-display-name-value="setDisplayNameValue"
      :field-value="fieldValue"
      :set-field-value="setFieldValue"
      @update:model-value="closePanel"
      @select-provider="setSelectedProvider"
      @select-method="setSelectedMethod"
      @start-provider="startProvider"
      @save-account="saveAccount"
      @go-plugins="router.push('/')"
    />

    <UiConfirmDialog
      :model-value="Boolean(pendingRevoke)"
      title="Revoke this Account?"
      :description="
        pendingRevoke
          ? `${pendingRevoke.display_name} will stop working everywhere. This cannot be undone.`
          : undefined
      "
      confirm-label="Revoke Account"
      cancel-label="Keep Account"
      tone="danger"
      :loading="
        Boolean(
          pendingRevoke &&
          busyAction === connectionActionKey(pendingRevoke.credential_ref, 'revoke'),
        )
      "
      @update:model-value="
        (open) => {
          if (!open) pendingRevoke = null
        }
      "
      @confirm="confirmRevoke"
      @cancel="pendingRevoke = null"
    />
  </UiPageShell>
</template>
