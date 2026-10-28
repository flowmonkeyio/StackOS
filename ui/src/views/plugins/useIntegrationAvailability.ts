import { ref } from 'vue'
import { callOperation } from '@/lib/operations'
import type { IntegrationAvailability } from './viewModel'

interface IntegrationInventory {
  project_id: number
  items: IntegrationAvailability[]
}

/** Feature-local inventory: unrelated catalog consumers do not fetch availability. */
export function useIntegrationAvailability() {
  const items = ref<IntegrationAvailability[]>([])
  const loading = ref(false)
  const error = ref(false)
  let generation = 0

  function reset(): void {
    generation += 1
    items.value = []
    loading.value = false
    error.value = false
  }

  async function refresh(projectId: number): Promise<void> {
    const request = ++generation
    items.value = []
    loading.value = true
    error.value = false
    try {
      const result = await callOperation<IntegrationInventory>('integration.list', {
        project_id: projectId,
        include_unavailable: true,
      })
      if (request !== generation) return
      if (result.project_id !== projectId || !Array.isArray(result.items)) {
        throw new Error('Integration inventory does not match the requested project.')
      }
      items.value = result.items
    } catch {
      if (request === generation) error.value = true
    } finally {
      if (request === generation) loading.value = false
    }
  }

  return { items, loading, error, refresh, reset }
}
