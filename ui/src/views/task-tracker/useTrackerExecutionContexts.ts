import { ref, type Ref } from 'vue'

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
  const error = ref<string | null>(null)
  let requestSeq = 0

  async function load(task: TrackerTask | null): Promise<void> {
    const currentRequest = ++requestSeq
    error.value = null
    if (!validProjectId(projectId.value) || !task) {
      reset()
      return
    }

    loading.value = true
    try {
      const page = await callOperation<TaskExecutionContextPage>('executionContext.list', {
        project_id: projectId.value,
        task_key: task.key,
        limit: 20,
      })
      if (currentRequest !== requestSeq) return

      contexts.value = page.items
      pageInfo.value = paginationInfo(page, 20)
      const entries = await Promise.all(page.items.map(loadArtifacts))
      if (currentRequest !== requestSeq) return

      artifacts.value = Object.fromEntries(
        entries.map(([contextRef, value]) => [contextRef, value.items]),
      )
      artifactPageInfo.value = Object.fromEntries(
        entries.map(([contextRef, value]) => [contextRef, value.info]),
      )
    } catch (cause) {
      if (currentRequest !== requestSeq) return
      reset()
      error.value = formatApiError(cause, 'failed to load task contexts')
    } finally {
      if (currentRequest === requestSeq) loading.value = false
    }
  }

  async function loadArtifacts(context: TaskExecutionContext) {
    if (!context.context_ref || (context.artifact_count ?? 0) === 0) {
      return [
        context.context_ref,
        { items: [] as TaskExecutionContextArtifact[], info: paginationInfo(null, 5) },
      ] as const
    }

    const page = await callOperation<TaskExecutionContextArtifactPage>(
      'executionContext.artifact.list',
      {
        project_id: projectId.value,
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
  }

  return {
    contexts,
    artifacts,
    pageInfo,
    artifactPageInfo,
    loading,
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
