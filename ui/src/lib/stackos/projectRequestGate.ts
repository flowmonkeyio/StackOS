export interface ProjectRequestToken {
  isCurrent: () => boolean
  finish: () => boolean
}

/**
 * One latest-wins contract for project-scoped store requests.
 *
 * Requests with different keys may run together inside one project. Changing
 * projects invalidates every request from the previous scope, while starting a
 * newer request with the same key invalidates only that operation.
 */
export function createProjectRequestGate(
  onScopeChange?: (projectId: number) => void,
) {
  let activeProjectId: number | null = null
  let scopeGeneration = 0
  let pending = 0
  const requestGenerations = new Map<string, number>()

  function begin(
    projectId: number,
    key = 'default',
    options: { trackPending?: boolean } = {},
  ): ProjectRequestToken {
    if (!Number.isSafeInteger(projectId) || projectId <= 0) {
      throw new TypeError('projectId must be a positive safe integer')
    }

    const scopeChanged = activeProjectId !== projectId
    if (scopeChanged) {
      activeProjectId = projectId
      scopeGeneration += 1
      pending = 0
      requestGenerations.clear()
      onScopeChange?.(projectId)
    }

    const tokenScopeGeneration = scopeGeneration
    const requestGeneration = (requestGenerations.get(key) ?? 0) + 1
    requestGenerations.set(key, requestGeneration)
    const tracksPending = options.trackPending ?? true
    if (tracksPending) pending += 1
    let finished = false

    return {
      isCurrent: () =>
        activeProjectId === projectId &&
        scopeGeneration === tokenScopeGeneration &&
        requestGenerations.get(key) === requestGeneration,
      finish: () => {
        if (!finished && tracksPending && scopeGeneration === tokenScopeGeneration) {
          pending = Math.max(0, pending - 1)
        }
        finished = true
        return pending > 0
      },
    }
  }

  function invalidate(): void {
    activeProjectId = null
    scopeGeneration += 1
    pending = 0
    requestGenerations.clear()
  }

  function invalidateOperation(key: string): void {
    requestGenerations.set(key, (requestGenerations.get(key) ?? 0) + 1)
  }

  return {
    begin,
    invalidate,
    invalidateOperation,
  }
}
