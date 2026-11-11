import { computed, onBeforeUnmount, onBeforeUpdate, ref, type Ref } from 'vue'
import type { components } from '@/api'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

/** Read the project owner's explicit selection, never infer one from workspaces. */
export function useProjectFolder(projectId: Ref<number>, onChange?: () => void) {
  const folder = ref('')
  const issue = ref('')
  const loading = ref(false)
  let loadedProjectId = projectId.value
  let generation = 0
  let disposed = false
  const projectDir = computed(() => loadedProjectId === projectId.value ? folder.value : '')
  const folderIssue = computed(() => loadedProjectId === projectId.value ? issue.value : '')

  async function refreshFolder() {
    const id = projectId.value
    loadedProjectId = id
    const request = ++generation
    folder.value = ''; issue.value = ''; loading.value = true
    onChange?.()
    try {
      const result = await callOperation<components['schemas']['ProjectOut']>('project.get', { project_id: id, response_mode: 'raw' })
      if (disposed || request !== generation || id !== projectId.value) return
      if (result.id !== id) throw new Error('Project folder belongs to a different project. Reload project setup.')
      folder.value = result.project_dir ?? ''
      issue.value = result.project_folder_issue ?? (folder.value ? '' : 'Set the project folder once in project setup using an existing workspace binding. Projects without a local folder can continue using other features.')
    } catch (cause) {
      if (!disposed && request === generation && id === projectId.value) issue.value = `Project folder could not be loaded. Retry or repair project setup. ${formatApiError(cause)}`
    } finally {
      if (!disposed && request === generation && id === projectId.value) { loading.value = false; onChange?.() }
    }
  }
  void refreshFolder()
  onBeforeUpdate(() => { if (loadedProjectId !== projectId.value) void refreshFolder() })
  onBeforeUnmount(() => { disposed = true; ++generation })
  return { projectDir, folderIssue, folderLoading: loading, refreshFolder }
}
