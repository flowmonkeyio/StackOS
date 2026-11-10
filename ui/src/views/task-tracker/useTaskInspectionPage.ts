import { onScopeDispose, ref, shallowRef, type Ref } from 'vue'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'

export interface TaskInspectionScope {
  projectId: Readonly<Ref<number>>
  taskKey: Readonly<Ref<string>>
}
interface SourceIdentity { source_kind: string; source_id: number }
export interface TaskInspectionPageState<T> {
  items: T[]
  nextCursor: string | null
  loading: boolean
  loaded: boolean
  error: string | null
  coverage: string
  warnings?: string[]
}
interface InspectionPage<T> {
  project_id: number
  task_key: string
  items: T[]
  next_cursor: string | null
  coverage: string
  warnings: string[]
}

/** The two task projections share opaque cursors and the same scope lifetime. */
export function useTaskInspectionPage<T extends SourceIdentity>(scope: TaskInspectionScope, operation: string, previewLimit: number) {
  const items = shallowRef<T[]>([])
  const nextCursor = ref<string | null>(null)
  const loading = ref(false), loaded = ref(false)
  const error = ref<string | null>(null), coverage = ref(''), warnings = ref<string[]>([])
  let generation = 0
  let disposed = false
  let lastAppend = false
  let selectedProject: number | null = null
  let selectedTask = ''

  function reset() {
    generation++
    items.value = []
    nextCursor.value = null
    error.value = null
    coverage.value = ''
    warnings.value = []
    loaded.value = false
    loading.value = false
    selectedProject = null
    selectedTask = ''
    lastAppend = false
  }
  async function read(append: boolean) {
    if (disposed) return
    const project = scope.projectId.value, key = scope.taskKey.value
    if (project !== selectedProject || key !== selectedTask) {
      reset()
      selectedProject = project
      selectedTask = key
      append = false
    }
    if (!key || !Number.isSafeInteger(project) || project <= 0) return
    if (append && (loading.value || nextCursor.value === null)) return
    const request = ++generation
    const current = () => !disposed && request === generation && project === scope.projectId.value && key === scope.taskKey.value
    const target = append ? 0 : items.value.length
    lastAppend = append
    let cursor = append ? nextCursor.value : null
    let rows: T[] = append ? [...items.value] : []
    let notes: string[] = append ? [...warnings.value] : []
    loading.value = true
    error.value = null
    try {
      do {
        const previousCursor = cursor
        const page = await callOperation<InspectionPage<T>>(operation, {
          project_id: project, task_key: key, limit: append || cursor !== null ? 50 : previewLimit, cursor,
        })
        if (!current()) return
        if (page.project_id !== project || page.task_key !== key) throw new Error('Task inspection response scope does not match the selected task.')
        const seen = new Set(rows.map(row => `${row.source_kind}:${row.source_id}`))
        rows = [...rows, ...page.items.filter(row => {
          const id = `${row.source_kind}:${row.source_id}`
          if (seen.has(id)) return false
          seen.add(id)
          return true
        })]
        notes = [...new Set([...notes, ...(page.warnings ?? [])])]
        cursor = page.next_cursor
        if (cursor !== null && cursor === previousCursor) throw new Error('Task inspection returned a repeated page cursor. Retry the read.')
        coverage.value = page.coverage
      } while (!append && cursor !== null && rows.length < target)
      items.value = rows
      warnings.value = notes
      nextCursor.value = cursor
      loaded.value = true
    } catch (cause) {
      if (current()) error.value = formatApiError(cause, 'Could not read task inspection')
    } finally {
      if (current()) loading.value = false
    }
  }
  const refresh = () => read(false)
  const more = () => read(true)
  const retry = () => read(lastAppend)
  onScopeDispose(() => { disposed = true; reset() })
  return { items, nextCursor, loading, loaded, error, coverage, warnings, refresh, more, retry, reset }
}
