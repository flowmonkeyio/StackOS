<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { UiBadge, UiButton, UiCallout, UiDialog, UiDropdownMenu, UiIcon, UiScoreMeter, UiSkeleton } from '@/components/ui'
import { accountHosts, useHomeHostAccounts } from './useHomeHostAccounts'
import HomeAccountEmail from './HomeAccountEmail.vue'

import { agentHostLabel, agentHostLogo, agentHostPresentation } from './agentHostPresentation'
import type { HostStatusState } from './useHomeAgentHostStatuses'
import type { HomeSubscriptions } from './useHomeSubscriptions'

const props = defineProps<{ state: HostStatusState; summary: string; subscriptions: HomeSubscriptions }>()
defineEmits<{ refresh: [] }>()
const accounts = useHomeHostAccounts()
onMounted(() => { void accounts.load() })
function email(host: string) {
  const state = accounts.hosts.value[host]
  const current = state?.accounts.find(account => account.account_ref === accounts.currentRef(host))
  return current?.email || 'Choose account'
}
function accountItems(host: string) {
  const state = accounts.hosts.value[host]
  return [
    ...(state?.accounts ?? []).map(account => ({
      key: account.account_ref, label: account.email,
      shortcut: account.account_ref === accounts.currentRef(host) ? 'Current' : undefined,
      disabled: accounts.busy.value[host],
      onSelect: () => { void accounts.select(host, account.account_ref) },
    })),
    { key: 'add', label: 'Add account', disabled: accounts.busy.value[host] || !!accounts.enrollment.value,
      onSelect: () => { void accounts.add(host) } },
  ]
}

type Meter = NonNullable<HomeSubscriptions['readings'][string]>['meters'][number]
const resetFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const percentFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })
const quotaHosts: Record<string, string> = {
  codex: 'Codex', 'claude-code': 'Claude Code', 'gemini-cli': 'Gemini CLI', grok: 'Grok Build',
}

function resetLines(meters: Meter[]) {
  const resets = meters.map(meter => {
    const ms = meter.resets_at ? Date.parse(meter.resets_at) : NaN
    const minute = Number.isFinite(ms) ? Math.round(ms / 60_000) * 60_000 : null
    return { scope: meter.scope, minute, at: meter.resets_at, text: minute === null ? null : resetFormat.format(minute) }
  })
  if (resets.length && resets.every(reset => reset.minute === resets[0]!.minute)) {
    return [{ ...resets[0]!, scope: '' }]
  }
  return resets
}

const cards = computed(() => {
  const hosts = new Map(props.state.items.map(host => [host.host_key, host]))
  const readings = props.subscriptions.readings
  // A failed inventory read still leaves any independent subscription result usable.
  if (props.state.kind === 'error') {
    for (const [key, reading] of Object.entries(readings)) {
      if (!hosts.has(key) && quotaHosts[key] && reading) hosts.set(key, { host_key: key, status_label: 'Status unavailable' })
    }
  }
  return [...hosts.values()].map(host => {
    const key = host.host_key
    const name = quotaHosts[key] ?? agentHostLabel(host)
    const reading = quotaHosts[key] ? readings[key] : null
    const meters = reading?.meters ?? []
    const status = agentHostPresentation(host)
    const waiting = !!quotaHosts[key] && key !== 'gemini-cli' && props.subscriptions.loading && !reading
    const quotaStatus = waiting ? 'Checking quota…' : 'Quota unavailable'
    let detail = host.repair || host.message
    if (status.state === 'connected') detail = undefined
    if (key === 'hermes' && status.state === 'available' && host.message?.includes('Choose a profile')) {
      detail = 'Choose a profile'
    }
    const plan = reading?.plan || (meters.length ? 'Plan unavailable' : undefined)
    return {
      key, name, logo: agentHostLogo(host), status, message: host.message,
      primary: plan || detail || (quotaHosts[key] ? quotaStatus : status.state === 'connected' ? 'MCP connected' : status.detail),
      note: plan && detail ? detail : (reading?.plan && !meters.length ? quotaStatus : undefined),
      meters: meters.map(({ scope, remaining_percent }) => ({
        scope, usedPercent: Number((100 - remaining_percent).toFixed(1)),
      })),
      resets: resetLines(meters), showResetPlaceholder: !meters.length && !waiting,
    }
  })
})

function meterTone(used: number) {
  if (used >= 95) return 'danger'
  if (used >= 75) return 'warning'
  return 'success'
}
</script>

<template>
  <section class="host-section border-t border-subtle pt-3" aria-label="AI tool connections">
    <div class="mb-3 flex items-center justify-between gap-3">
      <div class="min-w-0">
        <h3 class="text-sm font-semibold text-fg-strong">AI tool connections</h3>
        <p class="text-xs text-fg-muted">{{ summary }}</p>
      </div>
      <UiButton variant="ghost" size="sm" icon-left="refresh"
        :loading="state.kind === 'loading' || subscriptions.loading" @click="$emit('refresh'); accounts.load()">
        Refresh
      </UiButton>
    </div>

    <UiCallout v-if="state.kind === 'error'" tone="warning" density="compact" class="mb-2">
      {{ state.message }}
      <span v-if="state.items.length"> Previously checked connections are shown.</span>
    </UiCallout>
    <UiCallout v-if="subscriptions.failed" tone="warning" density="compact" class="mb-2">
      Couldn’t refresh subscription quotas.
    </UiCallout>
    <ul v-if="state.kind === 'loading' && !cards.length" class="host-grid" aria-hidden="true">
      <li v-for="slot in 6" :key="slot" class="min-h-32 rounded-md border border-subtle bg-bg-surface-alt p-3">
        <UiSkeleton shape="line" width="70%" />
        <UiSkeleton shape="line" width="45%" class="mt-2" />
      </li>
    </ul>
    <ul v-else-if="cards.length" class="host-grid">
      <li v-for="card in cards" :key="card.key" :aria-label="card.name"
        class="host-card min-w-0 rounded-lg border border-subtle bg-bg-surface-alt p-3">
        <div class="flex min-w-0 items-center gap-1.5">
          <img :src="card.logo" alt="" :class="accountHosts.has(card.key) ? 'h-9 w-9' : 'h-6 w-6'" class="shrink-0 rounded-md object-contain">
          <div class="min-w-0 flex-1">
            <h4 class="text-xs font-semibold leading-tight text-fg-strong">{{ card.name }}</h4>
            <UiDropdownMenu v-if="accountHosts.has(card.key)" :items="accountItems(card.key)" :aria-label="card.name + ' accounts'" class="account-picker w-full min-w-0">
              <template #trigger="{ open, toggle }">
                <button type="button" data-dropdown-trigger class="focus-ring flex w-full min-w-0 items-center gap-1 text-left text-xs text-fg-muted"
                  aria-haspopup="menu" :aria-expanded="open" :aria-label="card.name + ' account: ' + email(card.key)"
                  :title="email(card.key)" :disabled="accounts.busy.value[card.key]" @click="toggle">
                  <HomeAccountEmail :key="email(card.key)" :email="email(card.key)" />
                  <UiIcon name="chevron-down" class="h-3 w-3 shrink-0" aria-hidden="true" />
                </button>
              </template>
            </UiDropdownMenu>
          </div>
          <UiBadge :tone="card.status.tone" size="sm" class="shrink-0" :title="card.message">{{ card.status.label }}</UiBadge>
        </div>
        <div class="text-xs text-fg-muted">
          <p :title="card.message">{{ card.primary }}</p>
          <p v-if="accounts.errors.value[card.key]" role="alert" class="text-danger-fg">{{ accounts.errors.value[card.key] }}</p>
        </div>
        <div class="space-y-2 pt-3">
          <div v-for="meter in card.meters" :key="meter.scope">
            <div class="mb-1 flex items-baseline justify-between gap-x-1 text-xs">
              <span>{{ meter.scope }}</span>
              <span class="shrink-0 tabular-nums">{{ percentFormat.format(meter.usedPercent) }}%</span>
            </div>
            <UiScoreMeter variant="linear" :value="meter.usedPercent" :tone="meterTone(meter.usedPercent)"
              :label="card.name + ' ' + meter.scope + ' quota used (%)'" no-label />
          </div>
          <p v-if="card.note" class="text-2xs text-fg-muted">{{ card.note }}</p>
        </div>
        <div class="host-resets mt-3 border-t border-subtle pt-1.5 text-2xs text-fg-muted">
          <p v-if="card.showResetPlaceholder" title="No quota reset date">N/A</p>
          <p v-for="reset in card.resets" :key="reset.scope">
            <span v-if="reset.scope">{{ reset.scope }} · </span>
            <template v-if="reset.text && reset.at">Resets <time :datetime="reset.at">{{ reset.text }}</time></template>
            <template v-else>Reset unavailable</template>
          </p>
        </div>
      </li>
    </ul>
    <UiDialog :model-value="!!accounts.enrollment.value" title="Add account" size="sm" static-backdrop
      @update:model-value="value => { if (!value) void accounts.cancel() }">
      <template v-if="accounts.enrollment.value">
        <p v-if="accounts.enrollment.value.value.status === 'pending'">Complete sign-in in the native browser or tool window. This account will be added after the tool confirms its identity.</p>
        <p v-else-if="accounts.enrollment.value.value.status === 'enrolled'">Account added. Choose it from the account menu to use it for new sessions.</p>
        <p v-else-if="accounts.enrollment.value.value.status === 'duplicate'">This account is already saved.</p>
        <p v-else>Sign-in {{ accounts.enrollment.value.value.status }}. You can close this window and try again.</p>
        <p v-if="accounts.errors.value[accounts.enrollment.value.host]" role="alert" class="mt-2 text-danger-fg">{{ accounts.errors.value[accounts.enrollment.value.host] }}</p>
      </template>
      <template #footer>
        <UiButton v-if="accounts.enrollment.value?.value.status === 'pending'" variant="secondary" :loading="accounts.busy.value[accounts.enrollment.value.host]" @click="accounts.complete()">Check sign-in</UiButton>
        <UiButton variant="ghost" :disabled="!!accounts.enrollment.value && accounts.busy.value[accounts.enrollment.value.host]" @click="accounts.cancel()">{{ accounts.enrollment.value?.value.status === 'pending' ? 'Cancel' : 'Close' }}</UiButton>
      </template>
    </UiDialog>
  </section>
</template>

<style scoped>
.host-section { container-type: inline-size; }
.host-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: .5rem; list-style: none; padding: 0; margin: 0; }
.host-card { display: grid; grid-row: span 4; grid-template-rows: subgrid; row-gap: 0; overflow-wrap: anywhere; }
@container (min-width: 24rem) { .host-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@container (min-width: 36.5rem) { .host-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@container (min-width: 74rem) { .host-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); } }
</style>
