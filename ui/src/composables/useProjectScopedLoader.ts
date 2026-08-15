import { onBeforeUnmount, type ComputedRef } from 'vue'

export interface ProjectScopedLoadContext {
  projectId: number
}

export interface ProjectScopedLoaderOptions {
  projectId: ComputedRef<number>
  load: (context: ProjectScopedLoadContext) => Promise<unknown> | unknown
  onScopeExit?: () => void
  immediate?: boolean
}

export function useProjectScopedLoader(options: ProjectScopedLoaderOptions) {
  let exited = false

  function exitScope(): void {
    if (exited) return
    exited = true
    options.onScopeExit?.()
  }

  async function refresh(): Promise<boolean> {
    const projectId = options.projectId.value
    if (!Number.isSafeInteger(projectId) || exited) return false

    await options.load({ projectId })
    return !exited && options.projectId.value === projectId
  }

  // Start during setup so project-scoped stores clear their previous scope
  // before the replacement route component can render.
  if (options.immediate !== false) void refresh()
  onBeforeUnmount(exitScope)

  return {
    refresh,
  }
}
