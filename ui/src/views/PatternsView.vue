<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import type { DataTableColumn } from '@/components/types'
import MarkdownView from '@/components/MarkdownView.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiConfirmDialog,
  UiDialog,
  UiEmptyState,
  UiFormField,
  UiIcon,
  UiInput,
  UiLoadingState,
  UiPageHeader,
  UiPageShell,
  UiSelect,
  UiTextarea,
} from '@/components/ui'
import {
  usePatterns,
  type PatternDetail,
  type PatternDraft,
  type PatternSummary,
} from '@/composables/usePatterns'
import { ApiError, formatApiError } from '@/lib/client'
import { formatAbsoluteDateTime, formatShortDate } from '@/lib/stackos/time'

const route = useRoute()
const router = useRouter()
let disposed = false
const patterns = usePatterns()
const {
  page,
  loading,
  error,
  query,
  source,
  tags,
  previous,
  detail,
  detailLoading,
  detailError,
  missing,
} = patterns
const selectedId = computed(() => (route.params.patternId ? Number(route.params.patternId) : null))
const filtered = computed(() => Boolean(query.value || tags.value.length || source.value !== 'all'))
const columns: DataTableColumn<PatternSummary>[] = [
  { key: 'title', label: 'Pattern', widthClass: 'w-2/5', cellClass: 'max-w-0' },
  { key: 'source_label', label: 'Source', widthClass: 'w-48' },
  { key: 'tags', label: 'Tags', cellClass: 'max-w-64' },
  { key: 'updated_at', label: 'Updated', widthClass: 'w-28', cellClass: 'whitespace-nowrap' },
]
const sourceOptions = [
  { value: 'all', label: 'All sources' },
  { value: 'stackos', label: 'StackOS' },
  { value: 'customized', label: 'Customized' },
  { value: 'custom', label: 'Custom' },
]
const editorOpen = ref(false)
const editing = ref<PatternDetail | null>(null)
const draft = ref<PatternDraft>({ key: '', title: '', summary: '', body: '', tags: [] })
const tagText = ref('')
const initialDraft = ref('')
const preview = ref(false)
const saving = ref(false)
const saveError = ref('')
const dirty = computed(
  () =>
    editorOpen.value &&
    JSON.stringify({ ...draft.value, tagText: tagText.value }) !== initialDraft.value,
)
const discardOpen = ref(false)
let discardResolve: ((value: boolean) => void) | null = null
const deleteOpen = ref(false)
const deleting = ref(false)
const deleteError = ref('')
const deleteTarget = ref<PatternDetail | null>(null)
const deleteDescription = computed(() => {
  const entry = deleteTarget.value
  return entry
    ? `Remove “${entry.title}” from Patterns for every project in this installation?${entry.source !== 'custom' ? ' This deletion persists across software updates.' : ''}`
    : ''
})

function openEditor(entry: PatternDetail | null) {
  editing.value = entry
  draft.value = entry
    ? {
        key: entry.key,
        title: entry.title,
        summary: entry.summary,
        body: entry.body,
        tags: [...entry.tags],
      }
    : { key: `pattern-${crypto.randomUUID()}`, title: '', summary: '', body: '', tags: [] }
  tagText.value = entry?.tags.join(', ') ?? ''
  initialDraft.value = JSON.stringify({ ...draft.value, tagText: tagText.value })
  saveError.value = ''
  preview.value = false
  editorOpen.value = true
}

function confirmDiscard(): Promise<boolean> {
  if (saving.value || deleting.value) return Promise.resolve(false)
  if (!dirty.value) return Promise.resolve(true)
  if (discardResolve) return Promise.resolve(false)
  discardOpen.value = true
  return new Promise((resolve) => {
    discardResolve = resolve
  })
}

function finishDiscard(discard: boolean) {
  discardOpen.value = false
  if (discard) editorOpen.value = false
  discardResolve?.(discard)
  discardResolve = null
}

async function closeEditor(open: boolean) {
  if (!open && (await confirmDiscard())) editorOpen.value = false
}

async function save() {
  if (saving.value) return
  saving.value = true
  saveError.value = ''
  try {
    const saved = await patterns.save(
      {
        ...draft.value,
        tags: tagText.value
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      },
      editing.value?.etag,
    )
    if (disposed) return
    editorOpen.value = false
    saving.value = false
    // Refresh the bounded list, retaining the operator's active filters.
    void patterns.filter({})
    if (selectedId.value === saved.id) await patterns.select(saved.id)
    else await router.push(`/patterns/${saved.id}`)
  } catch (err) {
    saveError.value =
      err instanceof ApiError && [409, 412].includes(err.status)
        ? 'This pattern changed elsewhere. Your edits are kept. Copy them before closing, then reload this page to read the current version.'
        : formatApiError(err)
  } finally {
    saving.value = false
  }
}

function requestDelete() {
  if (!detail.value) return
  deleteTarget.value = detail.value
  deleteError.value = ''
  deleteOpen.value = true
}

async function remove() {
  if (!deleteTarget.value || deleting.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await patterns.remove(deleteTarget.value)
    if (disposed) return
    deleteOpen.value = false
    deleteTarget.value = null
    deleting.value = false
    await router.push('/patterns')
    await patterns.filter({})
  } catch (err) {
    deleteError.value =
      err instanceof ApiError && [409, 412].includes(err.status)
        ? 'This pattern changed elsewhere. Cancel and reload the page before deleting it.'
        : formatApiError(err)
  } finally {
    deleting.value = false
  }
}

function addTag(tag: string) {
  if (!tags.value.includes(tag)) void patterns.filter({ tags: [...tags.value, tag] })
}

function browseTag(tag: string) {
  addTag(tag)
  void router.push('/patterns')
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value && !saving.value && !deleting.value) return
  event.preventDefault()
  event.returnValue = ''
}

onBeforeRouteLeave(() => confirmDiscard())
onBeforeRouteUpdate(async (to) => {
  if (!(await confirmDiscard())) return false
  editorOpen.value = false
  deleteOpen.value = false
  void patterns.select(to.params.patternId ? Number(to.params.patternId) : null)
})
onMounted(() => {
  void patterns.load()
  void patterns.select(selectedId.value)
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('beforeunload', beforeUnload)
  discardResolve?.(false)
})
</script>

<template>
  <UiPageShell>
    <template v-if="selectedId !== null">
      <UiButton
        variant="ghost"
        icon-left="arrow-left"
        @click="router.push('/patterns')"
      >
        Back to Patterns
      </UiButton>
      <UiLoadingState
        v-if="detailLoading"
        label="Loading pattern…"
      />
      <UiEmptyState
        v-else-if="missing"
        title="Pattern not found"
        description="This entry may have been removed."
      />
      <UiCallout
        v-else-if="detailError"
        tone="danger"
        title="Could not load pattern"
      >
        {{ detailError }}
        <UiButton
          size="sm"
          @click="patterns.select(selectedId)"
        >
          Retry
        </UiButton>
      </UiCallout>
      <template v-else-if="detail">
        <UiPageHeader
          class="pattern-detail-header"
          :title="detail.title"
          :description="detail.summary"
        >
          <template #actions>
            <UiButton @click="openEditor(detail)">
              Edit pattern
            </UiButton>
            <UiButton
              variant="ghost"
              @click="requestDelete"
            >
              Delete pattern
            </UiButton>
          </template>
        </UiPageHeader>
        <div class="flex flex-wrap items-center gap-2">
          <UiBadge>{{ detail.source_label }}</UiBadge>
          <button
            v-for="tag in detail.tags"
            :key="tag"
            type="button"
            class="focus-ring rounded-xs bg-bg-sunken px-2 py-1 text-xs text-fg-default"
            @click="browseTag(tag)"
          >
            {{ tag }}
          </button>
          <span class="text-xs text-fg-muted">Updated {{ formatAbsoluteDateTime(detail.updated_at) }}</span>
        </div>
        <article
          class="mx-auto w-full max-w-3xl min-w-0 break-words py-4"
          aria-label="Pattern guidance"
        >
          <MarkdownView :source="detail.body" />
        </article>
      </template>
    </template>
    <template v-else>
      <UiPageHeader
        title="Patterns"
        description="Reusable guidance for every project."
      >
        <template #actions>
          <UiButton
            variant="primary"
            icon-left="plus"
            @click="openEditor(null)"
          >
            Add pattern
          </UiButton>
        </template>
      </UiPageHeader>
      <div class="flex items-center gap-3">
        <UiInput
          :model-value="query"
          type="search"
          placeholder="Find a pattern"
          aria-label="Find a pattern"
          class="flex-1"
          @update:model-value="patterns.filter({ query: String($event ?? '') })"
        >
          <template #prefix>
            <UiIcon
              name="search"
              class="h-4 w-4"
            />
          </template>
        </UiInput>
        <div class="w-48 shrink-0">
          <UiSelect
            :model-value="source"
            :options="sourceOptions"
            aria-label="Pattern source"
            @update:model-value="patterns.filter({ source: String($event) })"
          />
        </div>
      </div>
      <div
        v-if="filtered"
        class="flex flex-wrap items-center gap-2"
        aria-label="Active filters"
      >
        <UiButton
          v-for="tag in tags"
          :key="tag"
          size="sm"
          :aria-label="`Remove tag ${tag}`"
          @click="patterns.filter({ tags: tags.filter((value) => value !== tag) })"
        >
          {{ tag }} ×
        </UiButton>
        <UiButton
          size="sm"
          variant="ghost"
          @click="patterns.filter({ query: '', source: 'all', tags: [] })"
        >
          Clear filters
        </UiButton>
      </div>
      <UiLoadingState
        v-if="loading"
        label="Loading patterns…"
      />
      <UiCallout
        v-else-if="error"
        tone="danger"
        title="Could not load patterns"
      >
        {{ error }} <UiButton
          size="sm"
          @click="patterns.load"
        >
          Retry
        </UiButton>
      </UiCallout>
      <template v-else-if="page">
        <UiEmptyState
          v-if="page.catalog_total === 0"
          title="No patterns yet"
          description="Add reusable guidance to share across your projects."
        />
        <UiEmptyState
          v-else-if="!page.items.length"
          title="No matching patterns"
          description="Try another search or clear the filters."
        />
        <DataTable
          v-else
          :items="page.items"
          :columns="columns"
          aria-label="Patterns"
          max-height="calc(100dvh - 300px)"
        >
          <template #cell:title="{ row }">
            <RouterLink
              :to="`/patterns/${row.id}`"
              class="focus-ring block truncate font-semibold text-fg-strong"
              :title="row.title"
            >
              {{ row.title }}
            </RouterLink>
            <p
              class="truncate text-fg-muted"
              :title="row.summary"
            >
              {{ row.summary }}
            </p>
          </template>
          <template #cell:source_label="{ row }">
            <UiBadge>{{ row.source_label }}</UiBadge>
          </template>
          <template #cell:tags="{ row }">
            <div class="flex flex-wrap gap-1">
              <button
                v-for="tag in row.tags"
                :key="tag"
                type="button"
                class="focus-ring max-w-40 truncate rounded-xs bg-bg-sunken px-2 py-1 text-xs"
                :title="tag"
                @click="addTag(tag)"
              >
                {{ tag }}
              </button>
            </div>
          </template>
          <template #cell:updated_at="{ row }">
            <time
              :datetime="row.updated_at"
              :title="formatAbsoluteDateTime(row.updated_at)"
            >{{ formatShortDate(row.updated_at) }}</time>
          </template>
        </DataTable>
        <div class="flex items-center justify-between gap-3 text-xs text-fg-muted">
          <span>{{ page.total_estimate }}
            {{ page.total_estimate === 1 ? 'pattern' : 'patterns' }}</span>
          <div class="flex gap-2">
            <UiButton
              size="sm"
              :disabled="!previous.length"
              @click="patterns.back"
            >
              Previous
            </UiButton><UiButton
              size="sm"
              :disabled="!page.next_cursor"
              @click="patterns.next"
            >
              Next
            </UiButton>
          </div>
        </div>
      </template>
    </template>

    <UiDialog
      :model-value="editorOpen"
      :title="editing ? 'Edit pattern' : 'Add pattern'"
      size="lg"
      layout="detail"
      @update:model-value="closeEditor"
    >
      <form
        id="pattern-editor"
        class="space-y-4"
        @submit.prevent="save"
      >
        <UiCallout
          v-if="saveError"
          tone="danger"
          title="Could not save pattern"
        >
          {{ saveError }}
        </UiCallout>
        <UiFormField
          label="Title"
          required
        >
          <UiInput
            v-model="draft.title"
            required
            :maxlength="200"
            :disabled="saving"
          />
        </UiFormField>
        <UiFormField label="Summary">
          <UiTextarea
            v-model="draft.summary"
            :maxlength="1000"
            :rows="2"
            :disabled="saving"
          />
        </UiFormField>
        <UiFormField
          label="Tags"
          help="Separate freeform tags with commas."
        >
          <UiInput
            v-model="tagText"
            :disabled="saving"
          />
        </UiFormField>
        <UiFormField
          label="Markdown guidance"
          required
          help="Explain when this applies and include examples. If a specific technology is required, link to its reference and show how to use it."
        >
          <UiTextarea
            v-model="draft.body"
            required
            :maxlength="200000"
            :rows="12"
            :disabled="saving"
          />
        </UiFormField>
        <UiButton
          size="sm"
          :aria-expanded="preview"
          @click="preview = !preview"
        >
          {{ preview ? 'Hide preview' : 'Preview Markdown' }}
        </UiButton>
        <div
          v-if="preview"
          class="min-w-0 break-words rounded-lg border border-default p-4"
          aria-label="Markdown preview"
        >
          <MarkdownView :source="draft.body" />
        </div>
      </form>
      <template #footer>
        <span
          v-if="dirty"
          class="mr-auto text-xs text-fg-muted"
        >Unsaved changes</span>
        <UiButton
          variant="ghost"
          :disabled="saving"
          @click="closeEditor(false)"
        >
          Cancel
        </UiButton>
        <UiButton
          variant="primary"
          type="submit"
          form="pattern-editor"
          :loading="saving"
        >
          Save pattern
        </UiButton>
      </template>
    </UiDialog>
    <UiConfirmDialog
      :model-value="discardOpen"
      title="Discard changes?"
      description="Your unsaved pattern edits will be lost."
      confirm-label="Discard changes"
      @update:model-value="finishDiscard(false)"
      @cancel="finishDiscard(false)"
      @confirm="finishDiscard(true)"
    />
    <UiConfirmDialog
      :model-value="deleteOpen"
      title="Delete pattern?"
      :description="deleteDescription"
      confirm-label="Delete pattern"
      tone="danger"
      :loading="deleting"
      @update:model-value="
        (value) => {
          if (!deleting) deleteOpen = value
        }
      "
      @confirm="remove"
    >
      <UiCallout
        v-if="deleteError"
        tone="danger"
        title="Could not delete pattern"
      >
        {{ deleteError }}
      </UiCallout>
    </UiConfirmDialog>
  </UiPageShell>
</template>

<style scoped>
.pattern-detail-header :deep(h1) {
  max-width: 100%;
  overflow: visible;
  overflow-wrap: anywhere;
  white-space: normal;
  text-overflow: clip;
}
</style>
