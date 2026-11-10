import { onBeforeUnmount, ref, type Ref } from 'vue'
import { formatApiError } from '@/lib/client'

interface EnablementActions {
  enable: (projectId: number, pluginSlug: string) => Promise<void>
  refresh: (projectId: number) => Promise<unknown>
}

/** Explicit setup mutation, isolated from catalog loading and project navigation. */
export function usePluginEnablement(projectId: Ref<number>, actions: EnablementActions) {
  const pendingSlug = ref<string | null>(null)
  const error = ref<string | null>(null)
  const message = ref<string | null>(null)
  let disposed = false
  const scopeId = projectId.value
  onBeforeUnmount(() => {
    disposed = true
    pendingSlug.value = null
    error.value = null
    message.value = null
  })

  async function enable(slug: string, name: string): Promise<void> {
    if (disposed || pendingSlug.value || projectId.value !== scopeId || !Number.isSafeInteger(projectId.value) || projectId.value <= 0) return
    const id = projectId.value
    const current = () => !disposed && projectId.value === id
    pendingSlug.value = slug
    error.value = null
    message.value = null
    try {
      await actions.enable(id, slug)
      if (!current()) return
      message.value = `${name} is enabled for this project.`
      await actions.refresh(id)
    } catch (err) {
      if (current()) {
        error.value = formatApiError(err, 'Could not enable this plugin. Refresh to check its current state before retrying.')
      }
    } finally {
      if (current()) pendingSlug.value = null
    }
  }

  return { pendingSlug, error, message, enable }
}
