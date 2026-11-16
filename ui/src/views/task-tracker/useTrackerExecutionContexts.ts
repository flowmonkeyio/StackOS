import { onScopeDispose, ref, type Ref } from 'vue'

import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import type { TrackerTask } from '@/lib/task-tracker/types'

import type {
  TaskExecutionContext,
  TaskExecutionContextArtifact,
  TaskExecutionContextArtifactPage,
  TaskExecutionContextArtifactPageInfo,
  TaskExecutionContextPage,
  TaskExecutionContextPageInfo,
} from './executionContextTypes'

interface UseTrackerExecutionContextsOptions {
  projectId: Readonly<Ref<number>>
}

export function useTrackerExecutionContexts({ projectId }: UseTrackerExecutionContextsOptions) {
  const contexts = ref<TaskExecutionContext[]>([])
  const artifacts = ref<Record<string, TaskExecutionContextArtifact[]>>({})
  const pageInfo = ref<TaskExecutionContextPageInfo | null>(null)
  const artifactPageInfo = ref<TaskExecutionContextArtifactPageInfo>({})
  const loading = ref(false)
  const artifactsLoading = ref(false)
  const error = ref<string | null>(null)
  let requestSeq = 0
  let disposed = false

  async function load(task: TrackerTask | null, includeArtifacts = true): Promise<void> {
    if (disposed) return
    const currentRequest = ++requestSeq
    const requestProject = projectId.value
    const current = () => !disposed && currentRequest === requestSeq && projectId.value === requestProject
    error.value = null
    if (!validProjectId(projectId.value) || !task) {
      reset()
      return
    }

    loading.value = true
    try {
      const page = await callOperation<TaskExecutionContextPage>('executionContext.list', {
        project_id: requestProject,
        task_key: task.key,
        limit: 20,
      })
      if (!current()) return

      contexts.value = page.items
      pageInfo.value = paginationInfo(page, 20)
      loading.value = false
      artifacts.value = {}
      artifactPageInfo.value = {}
      if (!includeArtifacts) return
      artifactsLoading.value = true
      let index = 0
      // Context facts are useful independently of file previews. Keep failures
      // local to a secondary pane and bound the existing per-context requests.
      await Promise.all(Array.from({ length: Math.min(4, page.items.length) }, async () => {
        while (current() && index < page.items.length) {
          const context = page.items[index++]!
          try {
            const [contextRef, value] = await loadArtifacts(context, requestProject)
            if (!current()) return
            artifacts.value = { ...artifacts.value, [contextRef]: value.items }
            artifactPageInfo.value = { ...artifactPageInfo.value, [contextRef]: value.info }
          } catch (cause) {
            if (current()) error.value = formatApiError(cause, 'Could not load context artifacts. Context facts are still available.')
          }
        }
      }))
    } catch (cause) {
      if (!current()) return
      reset()
      error.value = formatApiError(cause, 'failed to load task contexts')
    } finally {
      if (currentRequest === requestSeq) {
        artifactsLoading.value = false
        if (projectId.value !== requestProject) reset()
        else loading.value = false
      }
    }
  }

  async function loadArtifacts(context: TaskExecutionContext, requestProject: number) {
    if (!context.context_ref || (context.artifact_count ?? 0) === 0) {
      return [
        context.context_ref,
        { items: [] as TaskExecutionContextArtifact[], info: paginationInfo(null, 5) },
      ] as const
    }

    const page = await callOperation<TaskExecutionContextArtifactPage>(
      'executionContext.artifact.list',
      {
        project_id: requestProject,
        context_ref: context.context_ref,
        limit: 5,
      },
    )
    return [context.context_ref, { items: page.items, info: paginationInfo(page, 5) }] as const
  }

  function reset(): void {
    contexts.value = []
    artifacts.value = {}
    pageInfo.value = null
    artifactPageInfo.value = {}
    loading.value = false
    artifactsLoading.value = false
  }

  onScopeDispose(() => { disposed = true; requestSeq++; reset() })

  return {
    contexts,
    artifacts,
    pageInfo,
    artifactPageInfo,
    loading,
    artifactsLoading,
    error,
    load,
  }
}

function paginationInfo(
  page: { next_cursor: number | null; total_estimate: number } | null,
  limit: number,
): TaskExecutionContextPageInfo {
  return {
    limit,
    nextCursor: page?.next_cursor ?? null,
    totalEstimate: page?.total_estimate ?? 0,
  }
}

function validProjectId(projectId: number): boolean {
  return Boolean(projectId) && !Number.isNaN(projectId)
}
