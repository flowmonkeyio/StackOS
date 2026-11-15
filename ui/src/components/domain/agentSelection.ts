import type { DesktopAgentExecutionCapabilities } from '@/lib/desktop'

// Model capability facts stay in hostMcp.status; source-native syntax options
// come from projectAgent responses. Loading a catalog never reconciles
// saved values; callers use this helper only for a deliberate selection change.
type Capabilities = Pick<DesktopAgentExecutionCapabilities, 'reasoning_efforts'> | undefined

export function reasoningChoices(capabilities: Capabilities, model: string | null, nativeEfforts?: string[]): string[] {
  const efforts = capabilities?.reasoning_efforts?.[model ?? '']
  return [...new Set(efforts ?? nativeEfforts ?? [])]
}

export function reasoningOptions(capabilities: Capabilities, model: string | null, effort: string | null, defaultLabel: string, nativeEfforts?: string[]) {
  const choices = reasoningChoices(capabilities, model, nativeEfforts)
  return [
    { value: '', label: defaultLabel },
    ...choices.map(value => ({ value, label: value })),
    ...(effort && !choices.includes(effort) ? [{ value: effort, label: `${effort} (not verified for this model)` }] : []),
  ]
}

export function reconcileReasoning(capabilities: Capabilities, model: string | null, effort: string | null, preserveInheritedEffort = false, nativeEfforts?: string[]): string | null {
  // An inherited model is unknown, and older hosts may omit capability data.
  // Neither proves an existing native source override incompatible.
  if (!effort || (!model && preserveInheritedEffort) || !capabilities?.reasoning_efforts) return effort
  return reasoningChoices(capabilities, model, nativeEfforts).includes(effort) ? effort : null
}
