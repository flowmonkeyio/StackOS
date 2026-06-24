<script setup lang="ts">
import { computed } from 'vue'

import { UiAdvancedJsonPanel, UiDescriptionList, UiSectionHeader } from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'
import { sanitizeForDisplay } from '@/lib/stackos/json'

const props = defineProps<{
  authStatus: unknown
}>()

interface DiagnosticsConnection {
  status?: string
  revoked_at?: string | null
}

const connections = computed<DiagnosticsConnection[]>(() => {
  const value = props.authStatus
  if (value && typeof value === 'object' && Array.isArray((value as { connections?: unknown }).connections)) {
    return (value as { connections: DiagnosticsConnection[] }).connections
  }
  return []
})

const activeConnections = computed(() =>
  connections.value.filter((connection) => connection.revoked_at == null),
)

const connectedCount = computed(
  () => activeConnections.value.filter((connection) => connection.status === 'connected').length,
)

const attentionCount = computed(
  () => activeConnections.value.filter((connection) => connection.status !== 'connected').length,
)

const overallStatus = computed(() => {
  if (activeConnections.value.length === 0) return 'pending'
  return attentionCount.value > 0 ? 'setup-required' : 'connected'
})
</script>

<template>
  <section
    v-if="authStatus"
    class="space-y-3"
    aria-label="Connection diagnostics"
  >
    <UiSectionHeader
      title="Diagnostics"
      description="Sanitized daemon-side auth status for support and verification."
      as="h3"
    >
      <template #actions>
        <StatusBadge
          domain="connection"
          :status="overallStatus"
          :label="
            activeConnections.length === 0
              ? 'No active connections'
              : `${connectedCount} connected`
          "
        />
      </template>
    </UiSectionHeader>

    <UiDescriptionList
      layout="grid"
      :columns="3"
      density="compact"
      numeric
      :items="[
        { label: 'Active connections', value: activeConnections.length },
        { label: 'Connected', value: connectedCount },
        { label: 'Needs attention', value: attentionCount },
      ]"
    />

    <UiAdvancedJsonPanel
      title="Raw auth status"
      summary="Sanitized daemon payload"
      :data="sanitizeForDisplay(authStatus)"
      max-height="34rem"
    />
  </section>
</template>
