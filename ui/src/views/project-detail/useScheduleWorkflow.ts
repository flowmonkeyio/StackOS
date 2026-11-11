import { onBeforeUnmount, onBeforeUpdate, ref, type Ref } from 'vue'
import type { SchemaLoadedWorkflowTemplate } from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'

export interface ScheduleWorkflowState {
  workflow: SchemaLoadedWorkflowTemplate | null
  loading: boolean
  error: string
}
// One effective-template request serves the input form and declared agent roles.
export function useScheduleWorkflow(projectId: Ref<number>, templateKey: Ref<string>, active: () => boolean = () => true) {
  const state = ref<ScheduleWorkflowState>({ workflow: null, loading: false, error: '' })
  let generation = 0
  const scope = () => JSON.stringify([projectId.value, templateKey.value, active()])
  let loadedScope = ''
  async function refresh() {
    const identity = scope()
    loadedScope = identity
    const request = ++generation
    const current = () => request === generation && identity === scope()
    state.value = { workflow: null, loading: !!templateKey.value && active(), error: '' }
    if (!state.value.loading) return
    try {
      const workflow = await apiFetch<SchemaLoadedWorkflowTemplate>(`/api/v1/projects/${projectId.value}/workflow-templates/${encodeURIComponent(templateKey.value)}`)
      if (current()) state.value = { workflow, loading: false, error: '' }
    } catch (cause) {
      if (current()) state.value = { workflow: null, loading: false, error: formatApiError(cause) }
    }
  }
  onBeforeUpdate(() => { if (loadedScope !== scope()) void refresh() })
  onBeforeUnmount(() => { ++generation })
  void refresh()
  return { state, refresh }
}
