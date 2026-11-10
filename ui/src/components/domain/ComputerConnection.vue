<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiButton, UiCallout, UiCard, UiSectionHeader } from '@/components/ui'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'

const props = defineProps<{ projectId: number }>()
const items = ref<Array<{ connection_id: string; status: string }>>([])
const error = ref('')
const loading = ref(false)
const setup = ref(false)
let generation = 0

async function load(): Promise<void> {
  const current = ++generation
  loading.value = true
  error.value = ''
  try {
    const result = await callOperation<{ items: typeof items.value }>('localRuntime.list', { project_id: props.projectId })
    if (current === generation) items.value = result.items
  } catch (err) {
    if (current === generation) error.value = formatApiError(err, 'Computer status unavailable.')
  } finally {
    if (current === generation) loading.value = false
  }
}
useProjectScopedLoader({
  projectId: computed(() => props.projectId),
  load,
  onScopeExit: () => { generation++ },
})
</script>

<template>
  <UiCard
    section
    aria-label="Local computer"
  >
    <UiSectionHeader
      title="Local computer"
      description="Browse this project here. Native agents run on your connected computer."
    >
      <template #actions>
        <UiButton
          size="sm"
          @click="setup = !setup"
        >
          Connect computer
        </UiButton>
        <UiButton
          size="sm"
          variant="ghost"
          :loading="loading"
          @click="load"
        >
          Refresh
        </UiButton>
      </template>
    </UiSectionHeader>
    <UiCallout
      v-if="error"
      tone="warning"
      class="mt-3"
    >
      {{ error }}
    </UiCallout>
    <p
      v-else
      class="mt-3 text-sm text-fg-muted"
    >
      {{ items.some(item => item.status === 'authorized') ? 'Computer authorized. Local runtime and agent readiness are checked before work starts.' : 'No authorized computer. Connect one to run native agents.' }}
    </p>
    <div
      v-if="setup"
      class="mt-3 space-y-2 text-sm"
    >
      <p>On this computer, select its private runtime environment file for this server, then sign in and choose an existing project folder in your terminal:</p>
      <pre class="overflow-x-auto rounded border border-subtle bg-sunken p-3 text-xs">export STACKOS_ENV_FILE=/path/to/runtime.env
stackos login
stackos connect --project-id {{ projectId }} --folder /path/to/existing/project</pre>
      <p>Keep that environment selected for CLI and MCP. Start the connected app or runtime, then refresh. Expired sign-in, revoked authorization and changed folders require explicit repair.</p>
    </div>
  </UiCard>
</template>
