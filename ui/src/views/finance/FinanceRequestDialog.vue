<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { UiAdvancedJsonPanel, UiButton, UiCallout, UiDialog, UiFactGroups, UiFormField, UiSelect } from '@/components/ui'
import { callOperation } from '@/lib/operations'
import { object, text } from '@/lib/finance'

const props = defineProps<{
  projectId: number
  workspaceRef: string
  requestId: number | null
  requests: Record<string, unknown>[]
}>()
const emit = defineEmits<{ close: []; refresh: [] }>()
const request = ref<Record<string, unknown> | null>(null)
const selectedId = ref<number | null>(null)
const loading = ref(false), error = ref('')
let generation = 0
onBeforeUnmount(() => { generation++ })

async function load(id: number | null) {
  const current = ++generation
  selectedId.value = id
  request.value = null
  error.value = ''
  loading.value = id !== null
  if (id === null) return
  try {
    const value = await callOperation<Record<string, unknown>>('agentRequest.get', {
      project_id: props.projectId, request_id: id, response_mode: 'raw',
    })
    if (current !== generation) return
    if (Number(value.id) !== id || (value.project_id !== undefined && value.project_id !== props.projectId)
      || object(object(value.metadata_json).metadata_json).workspace_ref !== props.workspaceRef) {
      throw new Error('Mismatched Finance request')
    }
    request.value = value
  } catch {
    if (current === generation) error.value = 'This Finance request is unavailable. Retry its read or refresh Finance to reconcile the queue.'
  } finally {
    if (current === generation) loading.value = false
  }
}

// The Finance origin mounts this reader only while open, keyed by project and
// original request. Leaving that scope unmounts it and invalidates late reads.
void load(props.requestId ?? (Number(props.requests[0]?.id) || null))
</script>

<template>
  <UiDialog
    :model-value="true"
    title="Finance analysis request"
    size="lg"
    layout="detail"
    @update:model-value="emit('close')"
  >
    <div class="space-y-4">
      <UiFormField
        v-if="requests.length"
        label="Analysis request"
      >
        <UiSelect
          aria-label="Analysis request"
          :model-value="String(selectedId ?? '')"
          :options="requests.map(row => ({ value: String(row.id), label: `Request ${row.id} · ${text(row.status)}` }))"
          @update:model-value="load(Number($event) || null)"
        />
      </UiFormField>
      <p
        v-if="loading"
        role="status"
      >
        Loading request…
      </p>
      <UiCallout
        v-else-if="error"
        variant="danger"
        role="alert"
      >
        {{ error }} <UiButton @click="load(selectedId)">
          Retry request read
        </UiButton>
      </UiCallout>
      <template v-else-if="request">
        <h2
          data-test="finance-request-title"
          class="t-h2 break-words"
        >
          {{ text(request.title) || `Request ${request.id}` }}
        </h2>
        <p class="text-sm text-fg-muted">
          {{ text(request.body_preview) }}
        </p>
        <UiFactGroups
          plain
          :groups="[{ title: 'Request status', items: [
            { label: 'Request', value: Number(request.id) },
            { label: 'Status', value: text(request.status) },
            { label: 'Claimed by', value: text(request.claimed_by) || 'Unclaimed' },
            { label: 'Run', value: request.run_plan_id ? `Run plan #${request.run_plan_id}` : 'No linked run' },
            { label: 'Updated', value: text(request.updated_at) },
          ] }]"
        />
        <p class="text-sm text-fg-muted">
          A request can exist before tracked work. Refresh analysis status in Finance to read its reviewed result. Inspection does not claim, retry or execute the request.
        </p>
        <UiAdvancedJsonPanel
          title="Details"
          summary="Original request and source"
          :data="request"
        />
      </template>
      <p
        v-else
        class="text-sm text-fg-muted"
      >
        No verified Finance request is available in this queue page. Refresh Finance to reconcile the original submission.
      </p>
      <UiButton @click="emit('refresh')">
        Refresh Finance requests
      </UiButton>
    </div>
  </UiDialog>
</template>
