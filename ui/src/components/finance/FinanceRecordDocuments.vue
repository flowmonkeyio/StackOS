<script setup lang="ts">
import { computed, onBeforeUnmount, onBeforeUpdate, ref } from 'vue'
import type { SchemaFinanceContentOut, SchemaFinanceRecordOut } from '@/api'
import { UiButton } from '@/components/ui'
import { callOperation } from '@/lib/operations'
import {
  canReadDocument,
  contentBlob,
  documentAvailabilityText,
  documentGroups,
  documentName,
  objects,
  refs,
  snapshotRef,
  text,
} from '@/lib/finance'
import type { FinanceRecordScope } from './useFinanceRecordEditor'

const props = defineProps<{
  detail: SchemaFinanceRecordOut
  scope: FinanceRecordScope
  open: boolean
}>()
const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'access-denied', cause: unknown): void
}>()
const base = () => ({
  project_id: props.scope.projectId,
  workspace_ref: props.scope.access.workspace_ref,
  access_generation: props.scope.access.generation,
})
const evidenceURL = ref(''),
  evidenceType = ref(''),
  evidenceName = ref(''),
  evidenceState = ref('')
const selectedAttachment = ref(''),
  selectedOriginalName = ref(''),
  originalReadFailed = ref(false)
const originalGroups = computed(() =>
  documentGroups(props.detail.evidence, props.detail.document_summary).filter(
    group => group.kind !== 'unidentified' || group.documents.length > 0,
  ),
)
let active = true,
  evidenceSequence = 0
function clearOriginal() {
  evidenceSequence++
  if (evidenceURL.value) URL.revokeObjectURL(evidenceURL.value)
  evidenceURL.value = ''
  evidenceType.value = ''
  evidenceName.value = ''
  evidenceState.value = ''
  selectedAttachment.value = ''
  selectedOriginalName.value = ''
  originalReadFailed.value = false
}
async function showOriginal(document: Record<string, unknown>) {
  if (
    !active ||
    !props.open ||
    props.scope.authorityCurrent === false ||
    !canReadDocument(document)
  )
    return
  clearOriginal()
  const sequence = evidenceSequence
  const identity = JSON.stringify([
    base(),
    props.scope.recordRef,
    snapshotRef(props.scope.snapshot),
  ])
  const current = () =>
    active &&
    props.open &&
    sequence === evidenceSequence &&
    identity === JSON.stringify([base(), props.scope.recordRef, snapshotRef(props.scope.snapshot)])
  selectedAttachment.value = text(document.attachment_ref)
  selectedOriginalName.value = documentName(document)
  evidenceState.value = `Opening ${selectedOriginalName.value}…`
  try {
    const value = await callOperation<SchemaFinanceContentOut>('finance.evidence.read', {
      ...base(),
      attachment_ref: selectedAttachment.value,
      expected_snapshot: snapshotRef(props.scope.snapshot),
      response_mode: 'raw',
    })
    if (!current()) return
    if (value.state !== 'available' || !value.content_base64) {
      originalReadFailed.value = true
      evidenceState.value =
        value.state === 'too_large'
          ? `${selectedOriginalName.value} cannot be opened here because it exceeds the reader limit. Ask your finance agent to inspect the original in the authorized finance workflow, then refresh.`
          : `${selectedOriginalName.value} cannot be opened. The original is unavailable or the source or access changed. Refresh to inspect current custody.`
      return
    }
    evidenceName.value = value.filename || 'original'
    evidenceType.value = value.inline_allowed ? value.content_type || '' : ''
    evidenceURL.value = URL.createObjectURL(
      contentBlob(value.content_base64, value.content_type || 'application/octet-stream'),
    )
    evidenceState.value = `Opened ${selectedOriginalName.value} · verified original`
  } catch (cause) {
    if (current()) {
      emit('access-denied', cause)
      originalReadFailed.value = true
      evidenceState.value = `${selectedOriginalName.value} cannot be opened. The original is unavailable or the source or access changed. Refresh to inspect current custody.`
    }
  }
}

let hadAuthority = props.scope.authorityCurrent !== false
onBeforeUpdate(() => {
  const authority = props.scope.authorityCurrent !== false
  if (!authority && hadAuthority) evidenceSequence++
  hadAuthority = authority
})
onBeforeUnmount(() => {
  active = false
  clearOriginal()
})
defineExpose({ clear: clearOriginal })
</script>

<template>
  <section
    class="mt-5 space-y-3"
    aria-label="Original documents"
  >
    <h3 class="t-h3">
      Documents
    </h3>
    <section
      v-for="group in originalGroups"
      :key="group.kind"
      :aria-label="group.label"
      class="space-y-2 border-t border-subtle pt-3"
    >
      <h4 class="font-medium">
        {{ group.label }} · {{ group.status }}
      </h4>
      <ul
        v-if="group.documents.length"
        class="space-y-3"
      >
        <li
          v-for="evidence in group.documents"
          :key="text(evidence.attachment_ref)"
          class="space-y-1"
        >
          <p class="break-words font-medium">
            {{ documentName(evidence) }}
          </p>
          <p class="text-xs text-fg-muted">
            {{ documentAvailabilityText(evidence) }}
          </p>
          <UiButton
            size="sm"
            :disabled="scope.authorityCurrent === false || !canReadDocument(evidence)"
            :aria-label="`${evidence.availability === 'available' ? 'Open' : 'Verify and open'} ${documentName(evidence)}`"
            :data-document-ref="text(evidence.attachment_ref)"
            @click="showOriginal(evidence)"
          >
            {{
              evidence.availability === 'available' ? 'Open original' : 'Verify and open original'
            }}
          </UiButton>
          <p
            v-if="selectedAttachment === evidence.attachment_ref && originalReadFailed"
            class="text-xs text-fg-muted"
          >
            Couldn’t open this document. Refresh its availability to try again.
          </p>
          <details class="text-xs text-fg-muted">
            <summary class="focus-ring cursor-pointer">
              Source details for {{ documentName(evidence) }}
            </summary>
            <p v-if="group.counts">
              {{ group.counts }}
            </p>
            <p v-if="evidence.media_type || typeof evidence.bytes === 'number'">
              {{ text(evidence.media_type)
              }}<template v-if="typeof evidence.bytes === 'number'">
                · {{ evidence.bytes }} bytes
              </template>
            </p>
            <p v-if="evidence.retention_state">
              Retention: {{ text(evidence.retention_state).replaceAll('_', ' ') }}
            </p>
            <p class="break-all">
              Attachment: {{ text(evidence.attachment_ref) }}
            </p>
            <p class="break-all">
              Declared sources: {{ refs(evidence.source_refs).join(', ') || 'Not supplied' }}
            </p>
            <p
              v-for="(context, index) in objects(evidence.source_context)"
              :key="`source-${index}`"
              class="break-all"
            >
              {{ context.historical ? 'Historical source' : 'Current source' }}:
              {{ text(context.source_ref) }} · requested {{ text(context.requested_ref) }} · current
              {{ text(context.current_ref) }}
            </p>
            <p
              v-for="(context, index) in objects(evidence.receipt_context)"
              :key="`receipt-${index}`"
              class="break-all"
            >
              {{ context.historical ? 'Historical receipt record' : 'Current receipt record' }}:
              {{ text(context.receipt_ref) }} · requested {{ text(context.requested_ref) }} ·
              current {{ text(context.current_ref) }}
            </p>
            <p
              v-if="evidence.sha256"
              class="break-all"
            >
              SHA-256: {{ text(evidence.sha256) }}
            </p>
            <p v-if="evidence.state">
              Recorded custody state: {{ text(evidence.state).replaceAll('_', ' ') }}
            </p>
          </details>
        </li>
      </ul>
    </section>
    <p class="text-xs text-fg-muted">
      You can ask your finance agent to attach another document.
    </p>
    <p
      v-if="evidenceState"
      role="status"
      class="break-words text-sm text-fg-muted"
    >
      {{ evidenceState }}
    </p>
    <UiButton
      v-if="originalReadFailed"
      size="sm"
      @click="emit('refresh')"
    >
      Refresh original availability
    </UiButton>
    <template v-if="evidenceURL">
      <a
        :href="scope.authorityCurrent === false ? undefined : evidenceURL"
        :aria-disabled="scope.authorityCurrent === false"
        :download="evidenceName"
        class="focus-ring text-fg-link"
      >Download {{ selectedOriginalName }}</a>
      <img
        v-if="evidenceType.startsWith('image/')"
        :src="evidenceURL"
        :alt="`Verified original: ${selectedOriginalName}`"
        class="max-w-full"
      >
      <iframe
        v-else-if="evidenceType === 'application/pdf'"
        :src="evidenceURL"
        sandbox=""
        :title="`Verified original: ${selectedOriginalName}`"
        class="h-96 w-full border border-default"
      />
    </template>
  </section>
</template>
