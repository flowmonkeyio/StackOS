import type { DesktopAgentExecutionCapabilities } from '@/lib/desktop'

// Capability facts stay in hostMcp.status. Loading a catalog never reconciles
// saved values; callers use this helper only for a deliberate selection change.
type Capabilities = Pick<DesktopAgentExecutionCapabilities, 'reasoning_efforts'> | undefined

export function reasoningChoices(capabilities: Capabilities, model: string | null): string[] {
  return [...new Set(capabilities?.reasoning_efforts?.[model ?? ''] ?? [])]
}

export function reasoningOptions(capabilities: Capabilities, model: string | null, effort: string | null, defaultLabel: string) {
  const choices = reasoningChoices(capabilities, model)
  return [
    { value: '', label: defaultLabel },
    ...choices.map(value => ({ value, label: value })),
    ...(effort && !choices.includes(effort) ? [{ value: effort, label: `${effort} (not verified for this model)` }] : []),
  ]
}

export function reconcileReasoning(capabilities: Capabilities, model: string | null, effort: string | null, preserveInheritedEffort = false): string | null {
  // An inherited model is unknown, and older hosts may omit capability data.
  // Neither proves an existing native source override incompatible.
  if (!effort || (!model && preserveInheritedEffort) || !capabilities?.reasoning_efforts) return effort
  return reasoningChoices(capabilities, model).includes(effort) ? effort : null
}
