<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiButton, UiCallout, UiDialog } from '@/components/ui'
import { formatApiError } from '@/lib/client'
import { useProjectsStore, type Project, type ProjectCleanupResult } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'

const props = defineProps<{
  project: Project
  finish: (projectId: number) => Promise<boolean>
  viewWork: (projectId: number) => Promise<boolean>
}>()
const emit = defineEmits<{ close: [] }>()
const projects = useProjectsStore()
const toasts = useToastsStore()
// The keyed dialog retains immutable identity across partial cleanup/retry.
const target = { id: props.project.id, slug: props.project.slug }
const pending = ref(false)
const pendingAction = ref<'delete' | 'navigation' | 'work'>('delete')
const result = ref<ProjectCleanupResult | null>(null)
const uncertain = ref(false)
const failure = ref<string | null>(null)
const confirmedName = ref<string | null>(null)
const incomplete = computed(() => uncertain.value || result.value?.status === 'incomplete' || Boolean(props.project.cleanup_json))
const removed = computed(() => result.value?.status === 'deleted' || result.value?.status === 'missing')
const displayedName = computed(() => pending.value || removed.value ? confirmedName.value ?? props.project.name : props.project.name)
const blocked = computed(() => result.value?.status === 'blocked')
const ownershipRequired = computed(() => Boolean(result.value?.ownership_required_artifact_ids?.length
  || (props.project.cleanup_json?.ownership_required_artifact_ids as unknown[] | undefined)?.length))
const sharedDependency = computed(() => result.value?.blockers.some((blocker) => blocker.startsWith('shared_')))
const title = computed(() => pending.value && pendingAction.value === 'delete' ? `Deleting ${displayedName.value}…`
  : removed.value ? `${displayedName.value} deleted`
    : incomplete.value ? `Couldn't finish deleting ${displayedName.value}` : `Delete ${displayedName.value}?`)

function close(): void { if (!pending.value) emit('close') }

async function completeNavigation(): Promise<void> {
  if (await props.finish(target.id)) {
    toasts.success(result.value?.status === 'missing' ? `${displayedName.value} is no longer available` : `${displayedName.value} deleted`)
    emit('close')
  } else failure.value = 'Project removed. Could not open the remaining project or Overview. Retry navigation.'
}

async function confirm(): Promise<void> {
  if (pending.value) return
  if (!removed.value) confirmedName.value = props.project.name
  pending.value = true
  pendingAction.value = removed.value ? 'navigation' : 'delete'
  failure.value = null
  try {
    if (!removed.value) {
      result.value = await projects.deleteProject(target)
      uncertain.value = false
    }
    if (removed.value) {
      pendingAction.value = 'navigation'
      await completeNavigation()
    }
  } catch (err) {
    // Transport errors cannot establish rollback or completion. Repeat only
    // the same confirmed operation; never search by the displayed name.
    uncertain.value = true
    failure.value = formatApiError(err, 'Could not confirm deletion. Retry to check this project.')
  } finally { pending.value = false }
}

async function openWork(): Promise<void> {
  if (pending.value) return
  pending.value = true
  pendingAction.value = 'work'
  try {
    if (await props.viewWork(target.id)) emit('close')
    else failure.value = 'Could not open active work. Try again.'
  } finally { pending.value = false }
}
</script>

<template>
  <UiDialog
    :model-value="true"
    :title="title"
    :description="target.slug"
    :static-backdrop="pending"
    :no-escape="pending"
    :hide-close="pending"
    scroll-body
    @update:model-value="close"
  >
    <div class="space-y-3 break-words">
      <template v-if="pending && pendingAction === 'delete'">
        <p role="status">
          Removing this project's data and managed files.
        </p>
        <p class="text-fg-muted">
          Repository folders will remain.
        </p>
      </template>
      <template v-else-if="removed">
        <p>This project is no longer available.</p>
        <p class="text-fg-muted">
          Repository folders remain untouched.
        </p>
      </template>
      <template v-else-if="incomplete">
        <p class="text-danger-fg">
          Some project data may already be removed. Retry to finish cleanup.
        </p>
        <p class="text-fg-muted">
          Repository folders remain untouched.
        </p>
        <p
          v-if="ownershipRequired"
          class="text-sm text-fg-muted"
        >
          Some legacy files need an administrator to confirm ownership before cleanup can finish.
        </p>
      </template>
      <template v-else-if="!blocked">
        <p>Permanently delete this project's StackOS records and managed files.</p>
        <p class="text-sm text-fg-muted">
          Includes tasks, runs, history, automations and saved information.
        </p>
        <p>Repository folders on your computer will remain.</p>
        <p class="text-sm text-fg-muted">
          This cannot be undone.
        </p>
      </template>
      <template v-else>
        <p>Repository folders on your computer will remain.</p>
      </template>
      <UiCallout
        v-if="blocked"
        tone="warning"
      >
        <template v-if="sharedDependency">
          Another project still uses this project's saved files or templates. Resolve those shared references before deleting.
        </template>
        <template v-else>
          Stop running work before deleting this project. Active browser sessions and file transfers must also finish or close.
        </template>
      </UiCallout>
      <p
        v-if="failure"
        role="alert"
        class="text-sm text-danger-fg"
      >
        {{ failure }}
      </p>
    </div>
    <template #footer>
      <UiButton
        variant="ghost"
        :disabled="pending"
        @click="close"
      >
        {{ incomplete || removed ? 'Close' : 'Cancel' }}
      </UiButton>
      <UiButton
        v-if="blocked && !sharedDependency"
        variant="primary"
        :disabled="pending"
        :loading="pending"
        @click="openWork"
      >
        View active work
      </UiButton>
      <UiButton
        v-else
        variant="danger"
        :disabled="pending"
        :loading="pending"
        @click="confirm"
      >
        {{ pending ? pendingAction === 'delete' ? 'Deleting…' : 'Opening…' : removed ? 'Retry navigation' : incomplete ? 'Retry cleanup' : blocked ? 'Retry deletion' : 'Delete project' }}
      </UiButton>
    </template>
  </UiDialog>
</template>
